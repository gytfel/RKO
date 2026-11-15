"""Opt-in discovery across public indexed web pages using Tavily Search API.

Uses a server-side API key and keeps a conservative daily request budget.
No scraping of protected services, personal contact collection or mass outreach.
"""
import ipaddress
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urldefrag, urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

from db import connect, get_setting, now
from notifications import new_opportunities
from scoring import score_text

log = logging.getLogger(__name__)
ENDPOINT = "https://api.tavily.com/search"
QUERIES = (
    '"где открыть расчетный счет" ИП форум',
    '"посоветуйте РКО" для ИП',
    '"какой банк выбрать для ИП" обсуждение',
    '"подскажите банк" "расчетный счет"',
    '"хочу открыть ИП" "какой банк"',
    '"открыть ООО" "расчетный счет" вопрос',
    '"какую дебетовую карту выбрать" форум',
    '"хочу оформить дебетовую карту" совет',
    'site:banki.ru "ИП" "банк" "посоветуйте"',
    'site:vc.ru "расчетный счет" "посоветуйте"',
    'site:hranidengi.com "дебетовую карту" "подскажите"',
    '"ищу банк для ИП" обсуждение',
)
QUERIES_PER_RUN = 5
DAILY_LIMIT = 24     # 24/day, <= 744 basic queries in a 31-day month.
RUN_EVERY_SECONDS = 6 * 3600
LOCK = threading.Lock()
STATUS = {"running": False, "added": 0, "checked": 0, "errors": [], "last_run": None}


def configured():
    return bool(os.getenv("TAVILY_API_KEY", "").strip())


def _setting(db, key, fallback=""):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else fallback


def _put(db, key, value):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, str(value)))


def _budget_snapshot():
    today = datetime.now(timezone.utc).date().isoformat()
    with connect() as db:
        used = int(_setting(db, "web_used", "0")) if _setting(db, "web_day") == today else 0
    return today, used


def _consume_credit():
    """Reserves one credit BEFORE the remote call (conservative on failures)."""
    today = datetime.now(timezone.utc).date().isoformat()
    with connect() as db:
        used = int(_setting(db, "web_used", "0")) if _setting(db, "web_day") == today else 0
        if used >= DAILY_LIMIT:
            return False
        _put(db, "web_day", today)
        _put(db, "web_used", used + 1)
    return True


def status():
    today, used = _budget_snapshot()
    with connect() as db:
        last = _setting(db, "web_last_run")
        added = int(_setting(db, "web_last_added", "0"))
        total = db.execute("SELECT COUNT(*) FROM leads WHERE origin='web'").fetchone()[0]
    return {
        "configured": configured(), "running": STATUS["running"],
        "last_run": last or None, "added": STATUS["added"] if STATUS["running"] else added,
        "checked": STATUS["checked"], "errors": list(STATUS["errors"])[:3],
        "used_today": used, "daily_limit": DAILY_LIMIT, "results": total,
        "queries_per_run": QUERIES_PER_RUN, "interval_hours": 6,
    }


def clean(value, limit):
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)[:limit]


def public_result_url(url):
    """Do not store private/special URLs or tracking query parameters."""
    try:
        p = urlsplit(str(url))
        if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
            return None
        hostname = p.hostname.lower()
        if hostname in ("localhost",) or hostname.endswith((".local", ".internal", ".test")):
            return None
        try:
            if not ipaddress.ip_address(hostname).is_global:
                return None
        except ValueError:
            pass
        clean_query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
        return urlunsplit(("https", p.netloc.lower(), p.path or "/", urlencode(clean_query), ""))[:2048]
    except (ValueError, TypeError):
        return None


def _search(query, key):
    result = requests.post(
        ENDPOINT,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={
            "query": query, "search_depth": "basic", "topic": "general",
            "max_results": 10, "time_range": "week",
            "include_answer": False, "include_raw_content": False, "include_images": False,
        },
        timeout=(5, 18),
    )
    if result.status_code in (401, 403):
        raise RuntimeError("Tavily: проверь API-ключ и доступ к тарифу.")
    if result.status_code in (402, 429):
        raise RuntimeError("Tavily: закончились кредиты или достигнут лимит.")
    result.raise_for_status()
    payload = result.json()
    if not isinstance(payload.get("results"), list):
        raise RuntimeError("Tavily вернул неожиданный ответ.")
    return payload["results"]


def run_once():
    if not configured() or not LOCK.acquire(blocking=False):
        return False
    # Local import prevents a circular dependency: DeepSeek reuses URL filters here.
    from deepseek_discovery import configured as ai_configured, analyze_lead
    STATUS.update(running=True, added=0, checked=0, errors=[])
    found = []
    candidates = []
    try:
        with connect() as db:
            cursor = int(_setting(db, "web_cursor", "0")) % len(QUERIES)
        key = os.environ["TAVILY_API_KEY"].strip()
        for i in range(QUERIES_PER_RUN):
            if not _consume_credit():
                STATUS["errors"].append("Достигнут дневной лимит поиска; повторим завтра.")
                break
            query = QUERIES[(cursor + i) % len(QUERIES)]
            try:
                for result in _search(query, key):
                    url = public_result_url(result.get("url"))
                    if not url:
                        continue
                    title = clean(result.get("title"), 250)
                    description = clean(result.get("content"), 1300)
                    if not title:
                        continue
                    cls = score_text(title, description)
                    if cls["intent_label"] == "information" or cls["category"] == "Другое":
                        continue
                    # These are suggestions, not proof of an actual bank application.
                    with connect() as db:
                        new = db.execute(
                            """INSERT OR IGNORE INTO leads
                               (source_id,title,description,url,category,score,reason,created_at,
                                published_at,intent_label,intent_summary,scored_version,origin)
                               VALUES (NULL,?,?,?,?,?,?,?,?,?,?,5,'web')""",
                            (title, description, url, cls["category"], cls["score"],
                             cls["reason"], now(), str(result.get("published_date") or "")[:120],
                             cls["intent_label"], cls["intent_summary"]),
                        )
                        if new.rowcount:
                            STATUS["added"] += 1
                            if cls["intent_label"] == "potential" and not ai_configured():
                                found.append({"title": title, "score": cls["score"], "url": url})
                            if ai_configured() and len(candidates) < 4:
                                candidates.append(new.lastrowid)
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                # Never log HTTP request headers (they contain the API key).
                STATUS["errors"].append(str(exc)[:180])
                log.warning("Web discovery query failed: %s", type(exc).__name__)
            STATUS["checked"] += 1
        with connect() as db:
            _put(db, "web_cursor", (cursor + STATUS["checked"]) % len(QUERIES))
            _put(db, "web_last_run", now())
            _put(db, "web_last_added", STATUS["added"])
        for lead_id in candidates:
            verdict = analyze_lead(lead_id)
            if verdict and verdict["verdict"] == "potential":
                with connect() as db:
                    row = db.execute("SELECT title, score, url FROM leads WHERE id=?", (lead_id,)).fetchone()
                if row:
                    found.append(dict(row))
        new_opportunities(found[:5])
    finally:
        STATUS["last_run"] = now()
        STATUS["running"] = False
        LOCK.release()
    return True


def start_refresh():
    if not configured() or LOCK.locked():
        return False
    threading.Thread(target=run_once, name="tavily-web-discovery", daemon=True).start()
    return True


def periodic_worker():
    time.sleep(60)
    while True:
        try:
            stamp = get_setting("web_last_run", "")
            due = True
            if stamp:
                last = datetime.fromisoformat(stamp)
                due = (datetime.now(timezone.utc) - last).total_seconds() >= RUN_EVERY_SECONDS
            if due:
                run_once()
        except Exception:
            log.exception("Web discovery scheduler failed")
        time.sleep(600)


def start_worker():
    if configured():
        threading.Thread(target=periodic_worker, name="web-discovery-scheduler", daemon=True).start()
