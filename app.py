import csv
import io
import os
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from collector import STATUS, start_refresh, start_worker, validate_public_url
from db import connect, get_setting, initialize, now, rescore_existing
from scoring import score_text
from notifications import configured as notifications_configured, send_message

app = Flask(__name__)
password = os.getenv("ADMIN_PASSWORD")
secret = os.getenv("APP_SECRET_KEY")
if not password or len(password) < 12 or not secret or len(secret) < 32:
    raise RuntimeError("Set ADMIN_PASSWORD (>=12 chars) and APP_SECRET_KEY (>=32 chars) in environment/.env")
app.secret_key = secret
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
                  PERMANENT_SESSION_LIFETIME=timedelta(days=30), SESSION_REFRESH_EACH_REQUEST=True,
                  SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=7), MAX_CONTENT_LENGTH=100_000)
PASSWORD_HASH = generate_password_hash(password)
FAILED_LOGINS = {}
LOGIN_LOCK = threading.Lock()
STAGES = {"new", "review", "contacted", "won", "not_relevant"}
CATEGORIES = {"РКО", "Регистрация бизнеса", "Маркетплейсы", "Карты", "Другое"}
initialize()
rescore_existing()
if os.getenv("DISABLE_SCHEDULER", "0") != "1":
    start_worker()

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response

def csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]

@app.context_processor
def inject_csrf():
    return {"csrf": csrf()}

def admin_only(handler):
    @wraps(handler)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            if request.path.startswith("/api"):
                return jsonify(error="Требуется авторизация"), 401
            return redirect(url_for("login"))
        if request.method not in ("GET", "HEAD", "OPTIONS") and not secrets.compare_digest(request.headers.get("X-CSRF-Token", "") or request.form.get("csrf", ""), session.get("csrf", "")):
            return jsonify(error="Неверный токен безопасности. Обновите страницу."), 403
        return handler(*args, **kwargs)
    return wrapper

def request_json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Ожидался JSON-объект")
    return data

def error(message, status=400):
    return jsonify(error=str(message)), status

@app.route("/login", methods=["GET", "POST"])
def login():
    csrf()
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("csrf", ""), session.get("csrf", "")):
            return render_template("login.html", error="Обновите страницу и попробуйте ещё раз"), 403
        ip = request.remote_addr or "unknown"
        with LOGIN_LOCK:
            attempts = [t for t in FAILED_LOGINS.get(ip, []) if time.time() - t < 900]
            FAILED_LOGINS[ip] = attempts
            limited = len(attempts) >= 10
        if limited:
            return render_template("login.html", error="Слишком много попыток. Попробуйте через 15 минут."), 429
        if check_password_hash(PASSWORD_HASH, request.form.get("password", "")):
            session.clear()
            session["auth"] = True
            # Persist for up to 30 days only on a device the user chooses to remember.
            # Flask signs the cookie; passwords and API keys are never placed in it.
            session.permanent = request.form.get("remember_device") == "yes"
            csrf()
            with LOGIN_LOCK:
                FAILED_LOGINS.pop(ip, None)
            return redirect(url_for("dashboard"))
        with LOGIN_LOCK:
            FAILED_LOGINS[ip].append(time.time())
        return render_template("login.html", error="Неверный пароль"), 401
    if session.get("auth"):
        return redirect(url_for("dashboard"))
    return render_template("login.html", error=None)

@app.route("/logout", methods=["POST"])
@admin_only
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@admin_only
def dashboard():
    return render_template("dashboard.html")

@app.route("/api/stats")
@admin_only
def stats():
    with connect() as db:
        total = db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        priority = db.execute("SELECT COUNT(*) FROM leads WHERE score>=65 AND stage IN ('new','review')").fetchone()[0]
        working = db.execute("SELECT COUNT(*) FROM leads WHERE stage IN ('review','contacted')").fetchone()[0]
        wins = db.execute("SELECT COUNT(*) FROM leads WHERE stage='won'").fetchone()[0]
        sources = db.execute("SELECT COUNT(*) FROM sources WHERE enabled=1").fetchone()[0]
        categories = [dict(x) for x in db.execute("SELECT category, COUNT(*) AS count FROM leads GROUP BY category ORDER BY count DESC")]
        opportunities = db.execute("SELECT COUNT(*) FROM leads WHERE intent_label='potential' AND stage IN ('new','review')").fetchone()[0]
    return jsonify(total=total, priority=priority, working=working, wins=wins, sources=sources,
                   categories=categories, opportunities=opportunities, refresh=dict(STATUS))

def lead_query_args():
    search = request.args.get("q", "").strip()[:120]
    category = request.args.get("category", "")
    stage = request.args.get("stage", "")
    min_score = request.args.get("min_score", get_setting("min_score", "35"))
    try:
        min_score = max(0, min(100, int(min_score)))
    except ValueError:
        min_score = 35
    where, params = ["l.score >= ?"], [min_score]
    if search:
        where.append("(l.title LIKE ? OR l.description LIKE ? OR l.notes LIKE ?)")
        params += [f"%{search}%"] * 3
    if category in CATEGORIES:
        where.append("l.category = ?")
        params.append(category)
    if stage in STAGES:
        where.append("l.stage = ?")
        params.append(stage)
    if request.args.get('only_potential') == '1':
        where.append("l.intent_label = 'potential'")
    return " AND ".join(where), params

@app.route("/api/leads")
@admin_only
def leads():
    where, params = lead_query_args()
    try:
        page = max(1, min(100000, int(request.args.get("page", "1"))))
    except ValueError:
        page = 1
    try:
        limit = max(1, min(30, int(request.args.get("limit", "30"))))
    except ValueError:
        limit = 30
    with connect() as db:
        count = db.execute(f"SELECT COUNT(*) FROM leads l WHERE {where}", params).fetchone()[0]
        rows = db.execute(f"""SELECT l.*, COALESCE(s.name,'Добавлено вручную') AS source_name
            FROM leads l LEFT JOIN sources s ON s.id=l.source_id
            WHERE {where} ORDER BY CASE WHEN l.stage='new' THEN 0 ELSE 1 END, l.score DESC, l.created_at DESC
            LIMIT ? OFFSET ?""", params + [limit, (page - 1) * limit]).fetchall()
    return jsonify(items=[dict(r) for r in rows], count=count, page=page, pages=max(1, (count + limit - 1)//limit))

@app.route("/api/leads/<int:lead_id>", methods=["PATCH", "DELETE"])
@admin_only
def update_lead(lead_id):
    if request.method == "DELETE":
        with connect() as db:
            db.execute("DELETE FROM leads WHERE id=?", (lead_id,))
        return jsonify(ok=True)
    try:
        data = request_json()
    except ValueError as exc:
        return error(exc)
    updates, values = [], []
    if "stage" in data:
        if data["stage"] not in STAGES:
            return error("Недопустимый статус")
        updates.append("stage=?")
        values.append(data["stage"])
    if "notes" in data:
        if not isinstance(data["notes"], str) or len(data["notes"]) > 4000:
            return error("Заметка не должна превышать 4000 символов")
        updates.append("notes=?")
        values.append(data["notes"])
    if not updates:
        return error("Нет изменений")
    with connect() as db:
        cur = db.execute(f"UPDATE leads SET {', '.join(updates)} WHERE id=?", values + [lead_id])
    if not cur.rowcount:
        return error("Запись не найдена", 404)
    return jsonify(ok=True)

@app.route("/api/manual", methods=["POST"])
@admin_only
def manual_lead():
    try:
        data = request_json()
    except ValueError as exc:
        return error(exc)
    title = str(data.get("title", "")).strip()[:250]
    url = str(data.get("url", "")).strip()[:2048]
    description = str(data.get("description", "")).strip()[:1300]
    if not title:
        return error("Введите название обсуждения")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return error("Нужна публичная HTTPS-ссылка на обсуждение")
    cls = score_text(title, description)
    try:
        with connect() as db:
            db.execute("""INSERT INTO leads (title,description,url,category,score,reason,created_at,intent_label,intent_summary,scored_version)
                        VALUES (?,?,?,?,?,?,?,?,?,4)""", (title, description, url, cls["category"], cls["score"], cls["reason"], now(), cls['intent_label'], cls['intent_summary']))
    except Exception as exc:
        if "UNIQUE" in str(exc):
            return error("Такая ссылка уже сохранена")
        raise
    return jsonify(ok=True), 201

@app.route("/api/sources", methods=["GET", "POST"])
@admin_only
def sources():
    if request.method == "GET":
        with connect() as db:
            items = [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY id DESC")]
        return jsonify(items=items)
    try:
        data = request_json()
        name = str(data.get("name", "")).strip()[:100]
        url = str(data.get("url", "")).strip()
        if not name:
            return error("Введите название источника")
        validate_public_url(url)
        with connect() as db:
            db.execute("INSERT INTO sources (name,url,enabled,created_at) VALUES (?,?,1,?)", (name, url, now()))
    except ValueError as exc:
        return error(exc)
    except Exception as exc:
        if "UNIQUE" in str(exc):
            return error("Этот RSS уже подключён")
        return error(exc)
    return jsonify(ok=True), 201

@app.route("/api/sources/<int:source_id>", methods=["PATCH", "DELETE"])
@admin_only
def source_edit(source_id):
    if request.method == "DELETE":
        with connect() as db:
            db.execute("UPDATE leads SET source_id=NULL WHERE source_id=?", (source_id,))
            db.execute("DELETE FROM sources WHERE id=?", (source_id,))
        return jsonify(ok=True)
    try:
        data = request_json()
    except ValueError as exc:
        return error(exc)
    if type(data.get("enabled")) is not bool:
        return error("Передайте enabled: true/false")
    with connect() as db:
        cur = db.execute("UPDATE sources SET enabled=? WHERE id=?", (int(data["enabled"]), source_id))
    if not cur.rowcount:
        return error("Источник не найден", 404)
    return jsonify(ok=True)

@app.route("/api/refresh", methods=["POST"])
@admin_only
def refresh():
    accepted = start_refresh()
    return jsonify(ok=True, started=accepted, message="Проверка запущена" if accepted else "Проверка уже выполняется")

@app.route("/api/settings", methods=["GET", "PATCH"])
@admin_only
def settings():
    if request.method == "GET":
        return jsonify(poll_minutes=int(get_setting("poll_minutes", "60")), min_score=int(get_setting("min_score", "35")))
    try:
        data = request_json()
        poll = int(data.get("poll_minutes", 60))
        threshold = int(data.get("min_score", 35))
        if not 15 <= poll <= 1440 or not 0 <= threshold <= 100:
            return error("Интервал 15–1440 минут; фильтр 0–100")
        with connect() as db:
            db.executemany("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", [("poll_minutes", str(poll)), ("min_score", str(threshold))])
    except (TypeError, ValueError) as exc:
        return error(f"Проверьте числовые значения: {exc}")
    return jsonify(ok=True)

@app.route('/api/diagnostics')
@admin_only
def diagnostics():
    with connect() as db:
        rows = db.execute("""SELECT s.id, s.name, s.enabled, s.checked_at, s.last_error,
            COUNT(l.id) AS publications,
            SUM(CASE WHEN l.intent_label='potential' THEN 1 ELSE 0 END) AS possible_requests
            FROM sources s LEFT JOIN leads l ON l.source_id=s.id
            GROUP BY s.id ORDER BY possible_requests DESC, publications DESC""").fetchall()
    return jsonify(items=[dict(r) for r in rows])

@app.route('/api/notifications', methods=['GET'])
@admin_only
def notification_status():
    return jsonify(configured=notifications_configured(), mode='Telegram — личные уведомления')

@app.route('/api/notifications/test', methods=['POST'])
@admin_only
def notification_test():
    ok, message = send_message('✅ RKO Radar v4: тестовое уведомление. Отправка работает.')
    if not ok:
        return error(message, 400)
    return jsonify(ok=True, message=message)

@app.route("/api/export")
@admin_only
def export_csv():
    where, params = lead_query_args()
    with connect() as db:
        rows = db.execute(f"""SELECT l.*, COALESCE(s.name,'Вручную') AS source_name FROM leads l
            LEFT JOIN sources s ON s.id=l.source_id WHERE {where} ORDER BY l.score DESC LIMIT 20000""", params).fetchall()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["ID", "Дата", "Источник", "Категория", "Оценка", "Статус", "Заголовок", "Описание", "Ссылка", "Причина", "Заметки"])
    def safe(cell):
        value = str(cell or "")
        # protect opening CSV in spreadsheet applications against formula injection
        if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            value = "'" + value
        return value
    for r in rows:
        writer.writerow([safe(r[key]) for key in ("id","created_at","source_name","category","score","stage","title","description","url","reason","notes")])
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=rko_radar_export.csv", "Cache-Control": "no-store"})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=False)
