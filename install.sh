#!/usr/bin/env bash
# RKO Radar v3. Local-only installer for Ubuntu/Debian with systemd.
set -Eeuo pipefail

c_info='\033[1;36m'; c_ok='\033[1;32m'; c_err='\033[1;31m'; c_off='\033[0m'
info() { printf "${c_info}➜${c_off} %s\n" "$*"; }
fail() { printf "${c_err}Ошибка:${c_off} %s\n" "$*" >&2; exit 1; }

if [[ ${EUID} -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then exec sudo bash "$0" "$@"; fi
  fail 'Нужно запустить от root (команда: su -).'
fi
[[ -r /etc/os-release ]] || fail 'Нужна Ubuntu или Debian.'
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) ;;
  *) [[ " ${ID_LIKE:-} " == *' debian '* ]] || fail 'Поддерживаются Ubuntu/Debian c systemd.' ;;
esac
command -v apt-get >/dev/null 2>&1 || fail 'Не найден apt-get.'
[[ "$(ps -p 1 -o comm= | tr -d ' ')" == systemd ]] || fail 'Для запуска требуется systemd (обычный VPS, не контейнер).'
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$SOURCE_DIR/app.py" && -f "$SOURCE_DIR/requirements.txt" && -f "$SOURCE_DIR/manage.sh" ]] || fail 'Не найден код RKO Radar внутри установочного пакета.'

APP_DIR=/opt/rko-radar
DATA_DIR=/var/lib/rko-radar
ENV_FILE=/etc/rko-radar.env
SERVICE_FILE=/etc/systemd/system/rko-radar.service
PORT_FILE=$APP_DIR/PORT
LOG_FILE=/var/log/rko-radar-install.log
export DEBIAN_FRONTEND=noninteractive
umask 077
printf '\n╔═════════════════════════════════════════════╗\n║      RKO RADAR — УСТАНОВКА НА VPS v3        ║\n╚═════════════════════════════════════════════╝\n\n'
info 'Шаг 1/5. Проверка Ubuntu/Debian и Python...'
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import venv,ensurepip' >/dev/null 2>&1; then
  apt-get update -qq >"$LOG_FILE" 2>&1 || fail "Ошибка apt-get update. Подробности: $LOG_FILE"
  apt-get install -y -qq python3 python3-venv python3-pip ca-certificates >>"$LOG_FILE" 2>&1 || fail "Ошибка установки Python. Подробности: $LOG_FILE"
fi
VENV_TEST="$(mktemp -d)"
if ! python3 -m venv "$VENV_TEST/check" >"$LOG_FILE" 2>&1; then
  apt-get update -qq >>"$LOG_FILE" 2>&1 || fail "Ошибка apt-get update. Лог: $LOG_FILE"
  apt-get install -y -qq python3-venv python3-pip >>"$LOG_FILE" 2>&1 || fail "Не установлен Python venv. Лог: $LOG_FILE"
fi
rm -rf "$VENV_TEST"

info 'Шаг 2/5. Подготовка программы (данные сохраняются)...'
if ! id -u rkoradar >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin --user-group rkoradar
fi
install -d -o root -g root -m 755 "$APP_DIR"
install -d -o rkoradar -g rkoradar -m 700 "$DATA_DIR"
NEW_PASSWORD=''
if [[ ! -f "$ENV_FILE" ]]; then
  read -r NEW_PASSWORD APP_SECRET_KEY < <(python3 -c 'import secrets; print(secrets.token_urlsafe(24),secrets.token_urlsafe(48))')
  cat >"$ENV_FILE" <<EOF
ADMIN_PASSWORD=$NEW_PASSWORD
APP_SECRET_KEY=$APP_SECRET_KEY
COOKIE_SECURE=0
DATA_DIR=$DATA_DIR
APP_PORT=8080
EOF
  chown root:root "$ENV_FILE"
  chmod 600 "$ENV_FILE"
else
  chmod 600 "$ENV_FILE"
fi

# Retain old app and live database. New code is prepared before stopping service.
STAGE_DIR="$(mktemp -d "$APP_DIR/.stage.XXXXXX")"
cleanup() { rm -rf "$STAGE_DIR"; }
trap cleanup EXIT
cp -a "$SOURCE_DIR"/app.py "$SOURCE_DIR"/collector.py "$SOURCE_DIR"/db.py "$SOURCE_DIR"/scoring.py "$SOURCE_DIR"/requirements.txt "$STAGE_DIR"/
cp -a "$SOURCE_DIR"/static "$SOURCE_DIR"/templates "$STAGE_DIR"/

info 'Шаг 3/5. Установка Python-библиотек...'
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$APP_DIR/.venv" >>"$LOG_FILE" 2>&1 || fail "Не удалось создать venv. Лог: $LOG_FILE"
fi
"$APP_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -q -r "$STAGE_DIR/requirements.txt" >>"$LOG_FILE" 2>&1 || fail "Ошибка загрузки библиотек. Проверь интернет на VPS. Лог: $LOG_FILE"
"$APP_DIR/.venv/bin/python" -m compileall -q "$STAGE_DIR" || fail 'В пакете ошибка синтаксиса Python.'

info 'Шаг 4/5. Настройка безопасного локального доступа...'
SAVED_PORT="$(sed -nE 's/^APP_PORT=([0-9]+)$/\1/p' "$ENV_FILE" | head -n1)"
[[ "$SAVED_PORT" =~ ^[0-9]+$ ]] || SAVED_PORT=8080
# Move traffic only after dependencies are successfully installed.
systemctl stop rko-radar.service >/dev/null 2>&1 || true
CHOSEN_PORT="$(python3 - "$SAVED_PORT" <<'PY'
import socket, sys
preferred = int(sys.argv[1]); options = [preferred] + [p for p in range(8080, 8091) if p != preferred]
for port in options:
    if not 1024 <= port <= 65535: continue
    with socket.socket() as s:
        try: s.bind(('127.0.0.1', port))
        except OSError: continue
        print(port); break
else:
    sys.exit(2)
PY
)" || fail 'Нет свободного порта 8080–8090.'
if grep -q '^APP_PORT=' "$ENV_FILE"; then
  sed -i "s/^APP_PORT=.*/APP_PORT=$CHOSEN_PORT/" "$ENV_FILE"
else
  printf '\nAPP_PORT=%s\n' "$CHOSEN_PORT" >>"$ENV_FILE"
fi
cp -f "$STAGE_DIR"/app.py "$STAGE_DIR"/collector.py "$STAGE_DIR"/db.py "$STAGE_DIR"/scoring.py "$STAGE_DIR"/requirements.txt "$APP_DIR"/
rm -rf "$APP_DIR/static" "$APP_DIR/templates"
cp -a "$STAGE_DIR/static" "$STAGE_DIR/templates" "$APP_DIR"/
chown -R root:root "$APP_DIR"
printf '%s\n' "$CHOSEN_PORT" >"$PORT_FILE"
chmod 644 "$PORT_FILE"

info 'Шаг 5/5. Запуск и проверка фоновой службы...'
cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=RKO Radar — анализ публичных RSS-публикаций
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rkoradar
Group=rkoradar
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/.venv/bin/gunicorn --workers 1 --threads 4 --timeout 60 --bind 127.0.0.1:$CHOSEN_PORT app:app
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF
install -m 755 "$SOURCE_DIR/manage.sh" /usr/local/bin/rko-radar
systemctl daemon-reload
systemctl enable --now rko-radar.service >/dev/null || fail 'Не удалось запустить службу. Выполни: sudo rko-radar logs'
sleep 2
if ! systemctl is-active --quiet rko-radar.service; then
  journalctl -u rko-radar.service -n 30 --no-pager >&2 || true
  fail 'Служба не запустилась. Выполни: sudo rko-radar logs'
fi
# A 5-second local HTTP probe catches cases where the service is active but app fails to answer.
python3 - "$CHOSEN_PORT" <<'PY' || fail 'Служба работает, но веб-панель не отвечает. Выполни: rko-radar logs'
from urllib.request import urlopen
import sys, time
url = 'http://127.0.0.1:' + sys.argv[1] + '/login'
for attempt in range(5):
    try:
        with urlopen(url, timeout=2) as r:
            assert r.status == 200
        break
    except Exception:
        if attempt == 4: raise
        time.sleep(1)
PY
printf '\n'
printf "${c_ok}✓ RKO Radar установлен и работает!${c_off}\n"
printf '────────────────────────────────────────────\n' 2>/dev/null || true
printf 'Адрес на сервере: http://127.0.0.1:%s\n' "$CHOSEN_PORT"
if [[ -n "$NEW_PASSWORD" ]]; then
  printf "${c_ok}Пароль панели: %s${c_off}\n" "$NEW_PASSWORD"
else
  printf 'Пароль сохранён: команда sudo rko-radar password\n'
fi
printf '\nДоступ с Windows (PowerShell на компьютере):\n'
printf '  ssh -N -L 18080:127.0.0.1:%s root@IP_ТВОЕГО_VPS\n' "$CHOSEN_PORT"
printf 'Открыть в браузере компьютера: http://127.0.0.1:18080\n'
printf '\nПроверка: sudo rko-radar doctor | Логи: sudo rko-radar logs\n'
printf 'Важно: порт доступен только внутри VPS. Не открывай его в интернет без HTTPS.\n'
