#!/usr/bin/env bash
# RKO Radar: replace redundant Caddy basic auth with signed 30-day application login.
# Safe for the existing TikTok app: only the RKO-RADAR-MANAGED block is changed.
set -Eeuo pipefail
umask 077
die() { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }
msg() { printf '\n== %s ==\n' "$*"; }
[[ "$EUID" -eq 0 ]] || die 'Запусти от root.'
CONTAINER='tiktok-grev--caddy-1'
DOMAIN='222-167-216-247.sslip.io'
ENV_FILE='/etc/rko-radar.env'
APP='/opt/rko-radar'
[[ -f "$ENV_FILE" && -f "$APP/app.py" && -f "$APP/PORT" ]] || die 'RKO Radar не найден.'
grep -Fq 'remember_device' "$APP/app.py" || die 'Сначала обнови программу из GitHub: sudo rko-radar upgrade'
command -v docker >/dev/null || die 'Docker не установлен.'
[[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" == true ]] || die 'Контейнер Caddy не работает.'
CONFIG="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")"
[[ -f "$CONFIG" ]] || die 'Не найден Caddyfile в bind mount.'
PORT="$(tr -d '[:space:]' <"$APP/PORT")"
[[ "$PORT" =~ ^[0-9]+$ ]] || die 'Некорректный порт приложения.'
systemctl is-active --quiet rko-radar.service || die 'Служба RKO Radar не активна.'
curl -fsS --max-time 6 "http://127.0.0.1:$PORT/login" >/dev/null || die 'Страница входа не отвечает.'

msg '1/4. Проверяю настройки'
if ! grep -Fq '# RKO-RADAR-MANAGED' "$CONFIG"; then
  die 'Не найден блок RKO в Caddyfile; ничего не меняю.'
fi
STAMP="$(date +%Y%m%d-%H%M%S)"
ENV_BACKUP="$ENV_FILE.rko-remember-$STAMP"
CFG_BACKUP="$CONFIG.rko-remember-$STAMP"
cp -p "$ENV_FILE" "$ENV_BACKUP"
cp -p "$CONFIG" "$CFG_BACKUP"
chmod 600 "$ENV_BACKUP" "$CFG_BACKUP"
NEWCFG="$(mktemp)"
HEADERS="$(mktemp)"
modified_env=0
modified_cfg=0
success=0
rollback() {
  ret=$?
  if [[ "$success" != 1 ]]; then
    printf 'Обнаружена ошибка: возвращаю предыдущие настройки...\n' >&2
    if [[ "$modified_cfg" == 1 ]]; then
      cat "$CFG_BACKUP" > "$CONFIG"
      docker exec "$CONTAINER" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || true
    fi
    if [[ "$modified_env" == 1 ]]; then
      cp -p "$ENV_BACKUP" "$ENV_FILE"
      systemctl restart rko-radar.service >/dev/null 2>&1 || true
    fi
  fi
  rm -f "$NEWCFG" "$HEADERS"
  exit "$ret"
}
trap rollback EXIT

python3 - "$CONFIG" "$NEWCFG" "$DOMAIN" <<'PY'
from pathlib import Path
import re
import sys
file, out, domain = sys.argv[1:]
text = Path(file).read_text(encoding="utf-8")
marker = "# RKO-RADAR-MANAGED"
if text.count(marker) != 1:
    raise SystemExit("RKO Caddy block must appear exactly once.")
start = text.index(marker)
domain_line = re.search(r"(?m)^" + re.escape(domain) + r" \{$", text[start:])
if not domain_line:
    raise SystemExit("RKO domain does not match the expected address.")
closing = text.find("\n}", start)
if closing < 0:
    raise SystemExit("Cannot determine end of RKO site block.")
end = closing + 2
block = text[start:end]
if block.count("basicauth") == 0:
    print("Дополнительная Caddy-авторизация уже отключена.")
    Path(out).write_text(text, encoding="utf-8")
    sys.exit(0)
pattern = re.compile(
    r"(?m)^[ \t]*basicauth[ \t]*\{[ \t]*\n"
    r"[ \t]*[A-Za-z0-9_.-]+[ \t]+\$2[aby]\$[^\n]+\n"
    r"[ \t]*\}[ \t]*\n"
)
updated, count = pattern.subn("    # Авторизация RKO Radar: защищённая сессия приложения\n", block)
if count != 1:
    raise SystemExit("Unexpected Caddy auth block; refusing to edit other sites.")
Path(out).write_text(text[:start] + updated + text[end:], encoding="utf-8")
PY

msg '2/4. Включаю защищённую cookie-сессию'
if grep -q '^COOKIE_SECURE=' "$ENV_FILE"; then
  sed -i 's/^COOKIE_SECURE=.*/COOKIE_SECURE=1/' "$ENV_FILE"
else
  printf '\nCOOKIE_SECURE=1\n' >> "$ENV_FILE"
fi
modified_env=1
systemctl restart rko-radar.service || die 'Не удалось перезапустить RKO Radar.'
curl -fsS --max-time 10 -D "$HEADERS" -o /dev/null "http://127.0.0.1:$PORT/login" || die 'Панель не отвечает после обновления cookie.'
grep -iqE '^set-cookie:.*[; ]Secure' "$HEADERS" || die 'Браузерная сессия пока не защищена флагом Secure.'
systemctl is-active --quiet rko-radar.service || die 'RKO Radar остановился.'
msg '3/4. Изменяю только блок RKO в существующем Docker Caddy'
docker cp "$NEWCFG" "$CONTAINER:/tmp/rko-remember-preview.Caddyfile"
docker exec "$CONTAINER" caddy validate --config /tmp/rko-remember-preview.Caddyfile --adapter caddyfile || die 'Новая конфигурация Caddy не прошла проверку.'
if ! cmp -s "$NEWCFG" "$CONFIG"; then
  cat "$NEWCFG" > "$CONFIG"
  modified_cfg=1
  docker exec "$CONTAINER" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || die 'Caddy отказался загружать конфигурацию.'
fi
success=1
msg '4/4. Готово'
printf 'Постоянный адрес: https://%s\n' "$DOMAIN"
printf 'Окно с именем пользователя radar отключено.\n'
printf 'Остался только пароль RKO Radar; отметь «Запомнить устройство» — до 30 дней.\n'
printf 'Для безопасности используй только личный телефон и не включай запоминание на чужом устройстве.\n'
printf 'Резервные копии: %s и %s\n' "$CFG_BACKUP" "$ENV_BACKUP"
