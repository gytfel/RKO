#!/usr/bin/env bash
# Configure permanent HTTPS access to the existing local-only RKO Radar app.
# Does not publish the database, tokens, or server secrets to GitHub.
set -Eeuo pipefail
umask 077

fail() { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }
info() { printf '\n== %s ==\n' "$*"; }
[[ $EUID -eq 0 ]] || fail 'Запусти под root: sudo bash /tmp/rko-public-web.sh'
[[ -f /etc/rko-radar.env && -f /opt/rko-radar/PORT ]] || fail 'RKO Radar не установлен: отсутствуют конфигурация или PORT.'
command -v systemctl >/dev/null || fail 'На VPS нужен systemd.'
command -v curl >/dev/null || fail 'Не найден curl: apt-get update && apt-get install -y curl'

PORT="$(tr -d '[:space:]' < /opt/rko-radar/PORT)"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail 'Неверный порт RKO Radar.'
(( PORT >= 1024 && PORT <= 65535 )) || fail 'Порт RKO Radar вне допустимого диапазона.'
systemctl is-active --quiet rko-radar.service || fail 'Служба RKO Radar неактивна; проверь rko-radar doctor.'
STATUS="$(curl --max-time 6 -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/login")" || fail 'Панель не отвечает локально.'
[[ "$STATUS" == 200 ]] || fail "Панель вернула HTTP $STATUS вместо 200."

info '1/4. Адрес сайта'
PUBLIC_IP="${PUBLIC_IP:-$(curl -4fsS --max-time 10 https://api.ipify.org || true)}"
[[ "$PUBLIC_IP" =~ ^([0-9]{1,3}[.]){3}[0-9]{1,3}$ ]] || fail 'Не удалось определить публичный IPv4. Запусти: PUBLIC_IP=ВАШ_IP bash /tmp/rko-public-web.sh'
python3 - "$PUBLIC_IP" <<'PY' || fail 'Указан не публичный IPv4.'
import ipaddress,sys
assert ipaddress.ip_address(sys.argv[1]).is_global
PY
DOMAIN="${PUBLIC_IP//./-}.sslip.io"
RESOLVED="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}')"
[[ "$RESOLVED" == "$PUBLIC_IP" ]] || fail "DNS для $DOMAIN не указывает на $PUBLIC_IP (ответ: ${RESOLVED:-нет})."
echo "Адрес: https://$DOMAIN"

info '2/4. Проверка веб-сервера'
for existing in nginx apache2 httpd; do
  if systemctl is-active --quiet "$existing" 2>/dev/null; then
    fail "На VPS уже запущен $existing. Не меняю существующую конфигурацию: нужна отдельная настройка прокси."
  fi
done
if ! command -v caddy >/dev/null 2>&1; then
  command -v apt-get >/dev/null 2>&1 || fail 'Автоматическая установка поддерживает Ubuntu/Debian.'
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy || fail 'Не удалось установить Caddy.'
fi

info '3/4. HTTPS и защита доступа'
CADDYFILE=/etc/caddy/Caddyfile
SITES=/etc/caddy/sites-enabled
SITEFILE="$SITES/rko-radar.caddy"
CREDS=/root/.rko-radar-web-access
mkdir -p "$SITES"
# The installer runs with umask 077; Caddy's systemd service runs as user caddy.
# Allow that service to traverse the imported site directory without exposing any password file.
getent group caddy >/dev/null || fail 'Системная группа caddy не найдена.'
chgrp caddy "$SITES"
chmod 750 "$SITES"
if [[ -f "$CREDS" ]]; then
  # Created by this installer with mode 600; root-only.
  # shellcheck disable=SC1090
  source "$CREDS"
else
  RKO_WEB_USER='radar'
  RKO_WEB_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(20))')"
  printf 'RKO_WEB_USER=%q\nRKO_WEB_PASS=%q\n' "$RKO_WEB_USER" "$RKO_WEB_PASS" > "$CREDS"
  chmod 600 "$CREDS"
fi
# Pass the password explicitly: some packaged Caddy versions return EOF on piped stdin.
# The value is not printed or written to the web config; only its bcrypt hash is.
PASSWORD_HASH="$(caddy hash-password --algorithm bcrypt --plaintext "$RKO_WEB_PASS")" || fail 'Не удалось создать хэш пароля. Проверь: caddy hash-password --help'
STAMP="$(date +%Y%m%d-%H%M%S)"
OLD_CADDY=""
OLD_SITE=""
if [[ -f "$CADDYFILE" ]]; then
  OLD_CADDY="$CADDYFILE.rko-backup-$STAMP"
  cp -a "$CADDYFILE" "$OLD_CADDY"
fi
if [[ -f "$SITEFILE" ]]; then
  OLD_SITE="$SITEFILE.rko-backup-$STAMP"
  cp -a "$SITEFILE" "$OLD_SITE"
fi
restore_on_error() {
  result=$?
  if (( result != 0 )); then
    echo 'Возвращаю прежнюю конфигурацию веб-сервера...' >&2
    if [[ -n "$OLD_CADDY" ]]; then cp -a "$OLD_CADDY" "$CADDYFILE"; else rm -f "$CADDYFILE"; fi
    if [[ -n "$OLD_SITE" ]]; then cp -a "$OLD_SITE" "$SITEFILE"; else rm -f "$SITEFILE"; fi
    systemctl reload caddy >/dev/null 2>&1 || true
  fi
}
trap restore_on_error EXIT
if [[ ! -f "$CADDYFILE" ]]; then
  printf '# Caddy server configuration for RKO Radar.\n' > "$CADDYFILE"
fi
if ! grep -Fq 'import /etc/caddy/sites-enabled/*.caddy' "$CADDYFILE"; then
  printf '\nimport /etc/caddy/sites-enabled/*.caddy\n' >> "$CADDYFILE"
fi
cat > "$SITEFILE" <<EOF
${DOMAIN} {
    encode gzip
    basic_auth {
        ${RKO_WEB_USER} ${PASSWORD_HASH}
    }
    reverse_proxy 127.0.0.1:${PORT}
}
EOF
# Config contains only a bcrypt hash, not the plaintext password.
# Make both the main config and the included file readable by the Caddy service.
chgrp caddy "$CADDYFILE" "$SITEFILE"
chmod g+r "$CADDYFILE"
chmod 640 "$SITEFILE"
if ! caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1; then
  # Caddy before v2.8 called basic_auth 'basicauth'.
  sed -i 's/    basic_auth {/    basicauth {/' "$SITEFILE"
fi
if ! caddy validate --config "$CADDYFILE" --adapter caddyfile; then
  fail "Неверная конфигурация Caddy; проверяй $CADDYFILE и $SITEFILE."
fi
systemctl enable --now caddy >/dev/null || { journalctl -u caddy -n 30 --no-pager >&2 || true; fail 'Не удалось запустить Caddy.'; }
systemctl reload caddy || systemctl restart caddy || { journalctl -u caddy -n 30 --no-pager >&2 || true; fail 'Не удалось применить настройки Caddy.'; }
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
fi

trap - EXIT
info '4/4. Результат'
printf 'Ссылка: https://%s\n' "$DOMAIN"
printf 'Дополнительная защита в браузере: логин %s, пароль %s\n' "$RKO_WEB_USER" "$RKO_WEB_PASS"
printf 'После этого введи привычный пароль RKO Radar.\n'
printf 'Окно терминала после установки можно закрыть.\n'
printf 'Если HTTPS не открылся, проверь внешние правила брандмауэра для TCP 80 и 443 и логи: journalctl -u caddy -n 40 --no-pager\n'
