#!/usr/bin/env bash
# RKO Radar — обновление уже установленного VPS из gyftel/RKO.
# Совместимо с установкой v3/v4 (Ubuntu/Debian + systemd).
set -Eeuo pipefail
umask 077

REPO_URL='https://github.com/gytfel/RKO.git'
REPO_BRANCH='main'
CHECKOUT='/opt/rko-radar-source'
APP='/opt/rko-radar'
DATA='/var/lib/rko-radar'
SERVICE='rko-radar.service'
BKDIR='/root/rko-radar-backups'
NEW_FILES=(app.py collector.py db.py scoring.py notifications.py telegram_setup.py requirements.txt)

fail() { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }
msg() { printf '\n== %s ==\n' "$*"; }
if (( EUID != 0 )); then
  if command -v sudo >/dev/null 2>&1; then exec sudo bash "$0" "$@"; fi
  fail 'Нужны права root.'
fi
[[ -f "$APP/app.py" && -f "$APP/PORT" && -x "$APP/.venv/bin/python" ]] || fail 'Не найдена действующая установка RKO Radar в /opt/rko-radar.'
[[ -d "$DATA" ]] || fail 'Нет каталога данных /var/lib/rko-radar.'
[[ -f /etc/rko-radar.env ]] || fail 'Нет файла настроек /etc/rko-radar.env.'
[[ -f /etc/systemd/system/rko-radar.service ]] || fail 'Нет службы systemd rko-radar.service.'
PORT="$(cat "$APP/PORT")"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail 'Некорректный файл PORT.'

msg '1/5. Получение исходников GitHub'
if ! command -v git >/dev/null 2>&1; then
  command -v apt-get >/dev/null 2>&1 || fail 'Не найден apt-get, установи git вручную.'
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git ca-certificates || fail 'Не удалось установить Git.'
fi
if [[ -d "$CHECKOUT/.git" ]]; then
  [[ "$(git -C "$CHECKOUT" remote get-url origin)" == "$REPO_URL" ]] || fail 'В $CHECKOUT уже есть другой Git-репозиторий; ничего не изменено.'
  [[ "$(git -C "$CHECKOUT" branch --show-current)" == "$REPO_BRANCH" ]] || fail 'Ожидается ветка main в $CHECKOUT.'
  git -C "$CHECKOUT" diff --quiet && git -C "$CHECKOUT" diff --cached --quiet || fail 'В каталоге исходников локальные изменения. Сначала сохрани их.'
  git -C "$CHECKOUT" pull --ff-only origin "$REPO_BRANCH" || fail 'Не удалось выполнить git pull.'
elif [[ ! -e "$CHECKOUT" ]]; then
  git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$CHECKOUT" || fail 'Не удалось клонировать GitHub.'
else
  fail "Папка $CHECKOUT уже существует, но не является Git-репозиторием."
fi
for f in "${NEW_FILES[@]}"; do [[ -f "$CHECKOUT/$f" ]] || fail "В репозитории отсутствует $f"; done
[[ -f "$CHECKOUT/manage.sh" && -f "$CHECKOUT/templates/login.html" && -f "$CHECKOUT/static/app.js" ]] || fail 'Неполный код веб-интерфейса.'

msg '2/5. Проверка кода и зависимостей'
"$APP/.venv/bin/python" -m compileall -q "$CHECKOUT/app.py" "$CHECKOUT/collector.py" "$CHECKOUT/db.py" "$CHECKOUT/scoring.py" "$CHECKOUT/notifications.py" "$CHECKOUT/telegram_setup.py" || fail 'Ошибка синтаксиса Python.'
"$APP/.venv/bin/python" -m pip install --disable-pip-version-check -q -r "$CHECKOUT/requirements.txt" || fail 'Ошибка установки зависимостей; действующий сервер не остановлен.'

TMP="$(mktemp -d /root/rko-github-update.XXXXXXXX)"
ROLLBACK="$TMP/previous"
mkdir -p "$ROLLBACK" "$BKDIR"
chmod 700 "$BKDIR"
SUCCESS=0
STOPPED=0
on_exit() {
  code=$?
  if (( SUCCESS != 1 && STOPPED == 1 )); then
    echo 'Ошибка обновления, восстанавливаю предыдущий код...'
    systemctl stop "$SERVICE" >/dev/null 2>&1 || true
    for f in "${NEW_FILES[@]}"; do
      if [[ -e "$ROLLBACK/$f" ]]; then cp -f "$ROLLBACK/$f" "$APP/$f"; else rm -f "$APP/$f"; fi
    done
    for d in templates static; do
      rm -rf "$APP/$d"
      [[ ! -d "$ROLLBACK/$d" ]] || cp -a "$ROLLBACK/$d" "$APP/$d"
    done
    if [[ -f "$ROLLBACK/manager" ]]; then install -m 755 "$ROLLBACK/manager" /usr/local/bin/rko-radar; fi
    systemctl daemon-reload || true
    systemctl start "$SERVICE" || true
    echo 'Предыдущий код восстановлен. База данных не удалялась.'
  fi
  rm -rf "$TMP"
  exit "$code"
}
trap on_exit EXIT

msg '3/5. Резервная копия (пароль и порт сохраняются)'
systemctl stop "$SERVICE" || fail 'Не удалось остановить службу.'
STOPPED=1
for f in "${NEW_FILES[@]}"; do [[ ! -f "$APP/$f" ]] || cp -a "$APP/$f" "$ROLLBACK/$f"; done
for d in templates static; do [[ ! -d "$APP/$d" ]] || cp -a "$APP/$d" "$ROLLBACK/$d"; done
[[ ! -f /usr/local/bin/rko-radar ]] || cp -a /usr/local/bin/rko-radar "$ROLLBACK/manager"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BKDIR/rko-before-github-$STAMP.tar.gz"
BACKUP_ITEMS=(var/lib/rko-radar etc/rko-radar.env)
[[ ! -f /etc/rko-radar-notify.env ]] || BACKUP_ITEMS+=(etc/rko-radar-notify.env)
tar -czf "$BACKUP" -C / "${BACKUP_ITEMS[@]}" || fail 'Не удалось создать резервную копию.'
chmod 600 "$BACKUP"

msg '4/5. Обновление кода и запуск'
for f in "${NEW_FILES[@]}"; do install -m 644 "$CHECKOUT/$f" "$APP/$f"; done
for d in templates static; do
  rm -rf "$APP/$d"
  cp -a "$CHECKOUT/$d" "$APP/$d"
  chmod -R a+rX "$APP/$d"
done
install -m 755 "$CHECKOUT/manage.sh" /usr/local/bin/rko-radar
chmod 755 "$APP" "$APP/.venv"
chmod -R a+rX "$APP/.venv"
mkdir -p /etc/systemd/system/rko-radar.service.d
cat > /etc/systemd/system/rko-radar.service.d/notify.conf <<'SYSTEMD'
[Service]
EnvironmentFile=-/etc/rko-radar-notify.env
SYSTEMD
systemctl daemon-reload
systemctl start "$SERVICE" || fail 'Служба не запустилась.'
"$APP/.venv/bin/python" - "$PORT" <<'PY' || fail 'Панель не отвечает. Проверь логи: rko-radar logs'
import sys,time
from urllib.request import urlopen
url='http://127.0.0.1:'+sys.argv[1]+'/login'
for attempt in range(15):
    try:
        with urlopen(url,timeout=2) as result:
            if result.status == 200:
                break
    except Exception:
        pass
    time.sleep(1)
else:
    raise SystemExit('HTTP 200 не получен: '+url)
PY
systemctl is-active --quiet "$SERVICE" || fail 'Служба перестала работать.'
SUCCESS=1

msg '5/5. Обновление завершено'
printf '✓ GitHub: %s (%s)\n' "$REPO_URL" "$(git -C "$CHECKOUT" rev-parse --short HEAD)"
printf '✓ RKO Radar работает. Порт VPS: %s\n' "$PORT"
printf '✓ База, источники, пароль и токен Telegram сохранены.\n'
printf '✓ Резервная копия: %s\n' "$BACKUP"
printf '✓ Дальше обновляй командой: rko-radar upgrade\n'
printf '✓ Состояние: rko-radar doctor\n'
printf '✓ Панель через SSH: http://127.0.0.1:18080 (локальный туннель -> %s)\n' "$PORT"
