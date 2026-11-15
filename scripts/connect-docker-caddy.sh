#!/usr/bin/env bash
# RKO Radar -> existing Docker Caddy, preserving the TikTok site.
set -Eeuo pipefail
umask 077
fail(){ echo "Ошибка: $*" >&2; exit 1; }
msg(){ printf '\n== %s ==\n' "$*"; }
[[ $EUID -eq 0 ]] || fail 'Нужны права root.'
C='tiktok-grev--caddy-1'
DOMAIN='222-167-216-247.sslip.io'
BRIDGE_PORT=18081
[[ -f /opt/rko-radar/PORT ]] || fail 'RKO Radar не установлен.'
PORT="$(tr -d '[:space:]' </opt/rko-radar/PORT)"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail 'Неверный порт приложения.'
systemctl is-active --quiet rko-radar.service || fail 'Служба RKO Radar неактивна.'
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/login" >/dev/null || fail 'Панель локально не отвечает.'
[[ "$(docker inspect --format '{{.State.Running}}' "$C" 2>/dev/null)" == true ]] || fail 'Docker Caddy не запущен.'
FILE="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$C")"
[[ -f "$FILE" ]] || fail 'Не найден Caddyfile, установленный как bind mount.'
GW="$(docker inspect --format '{{range .NetworkSettings.Networks}}{{println .Gateway}}{{end}}' "$C" | head -1 | tr -d '[:space:]')"
[[ "$GW" =~ ^([0-9]{1,3}[.]){3}[0-9]{1,3}$ ]] || fail 'Не удалось определить шлюз Docker.'
ip -4 addr show | grep -Fq "$GW/" || fail "Адрес шлюза $GW отсутствует на VPS."
EXISTING_DOMAIN="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$C" | sed -n 's/^DOMAIN=//p' | head -1)"
[[ "$EXISTING_DOMAIN" != "$DOMAIN" ]] || fail 'Новый адрес уже занят основным сайтом TikTok.'
if grep -Fq "$DOMAIN {" "$FILE" || grep -Fq 'RKO-RADAR-MANAGED' "$FILE"; then
  fail 'В Caddyfile уже имеется RKO-сайт. Не добавляю дубликат.'
fi
[[ "$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')" == '222.167.216.247' ]] || fail 'DNS sslip.io пока не указывает на этот VPS.'
PROXY_BIN=''
for b in /usr/lib/systemd/systemd-socket-proxyd /lib/systemd/systemd-socket-proxyd; do
  if [[ -x "$b" ]]; then PROXY_BIN="$b"; break; fi
done
[[ -n "$PROXY_BIN" ]] || fail 'Компонент systemd-socket-proxyd отсутствует.'
msg '1/4. Внутренний мост к RKO Radar'
SOCK='/etc/systemd/system/rko-radar-bridge.socket'
SVC='/etc/systemd/system/rko-radar-bridge.service'
if [[ -e "$SOCK" || -e "$SVC" ]]; then
  [[ -f "$SOCK" && -f "$SVC" ]] || fail 'Есть незавершённая настройка моста.'
  grep -Fq "ListenStream=$GW:$BRIDGE_PORT" "$SOCK" || fail 'Существующий мост настроен иначе.'
  grep -Fq "127.0.0.1:$PORT" "$SVC" || fail 'Существующий мост ведёт на другой порт.'
else
  cat >"$SOCK" <<EOF
[Unit]
Description=RKO Radar internal Docker bridge socket
[Socket]
ListenStream=$GW:$BRIDGE_PORT
FreeBind=yes
NoDelay=true
[Install]
WantedBy=sockets.target
EOF
  cat >"$SVC" <<EOF
[Unit]
Description=RKO Radar internal Docker bridge proxy
Requires=rko-radar.service
After=rko-radar.service
[Service]
Type=exec
ExecStart=$PROXY_BIN 127.0.0.1:$PORT
NoNewPrivileges=true
PrivateTmp=true
EOF
fi
systemctl daemon-reload
systemctl enable --now rko-radar-bridge.socket >/dev/null || fail 'Не удалось включить мост.'
curl -fsS --max-time 8 "http://$GW:$BRIDGE_PORT/login" >/dev/null || fail 'Внутренний мост не отвечает.'
msg '2/4. Проверка связи из Docker'
docker exec "$C" sh -c "wget -q -T 8 -O /dev/null http://$GW:$BRIDGE_PORT/login" || fail 'Caddy-контейнер не может соединиться с RKO Radar; TikTok не изменён.'
msg '3/4. Добавление отдельного HTTPS-адреса в существующий Caddy'
CREDS='/root/.rko-radar-web-access'
if [[ -f "$CREDS" ]]; then
  # This credential file was created by the earlier RKO installer on this VPS.
  source "$CREDS"
else
  RKO_WEB_USER='radar'
  RKO_WEB_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(22))')"
  printf 'RKO_WEB_USER=%q\nRKO_WEB_PASS=%q\n' "$RKO_WEB_USER" "$RKO_WEB_PASS" > "$CREDS"
  chmod 600 "$CREDS"
fi
[[ -n "$RKO_WEB_USER" && -n "$RKO_WEB_PASS" ]] || fail 'Пустые данные входа для Caddy.'
command -v caddy >/dev/null || fail 'В системе не найдена команда caddy.'
HASH="$(caddy hash-password --algorithm bcrypt --plaintext "$RKO_WEB_PASS")" || fail 'Не удалось получить bcrypt-хэш.'
BACKUP="$FILE.rko-backup-$(date +%Y%m%d-%H%M%S)"
cp -a "$FILE" "$BACKUP"
TMP="$(mktemp)"
cp "$FILE" "$TMP"
cat >> "$TMP" <<EOF

# RKO-RADAR-MANAGED - additional site, leave TikTok config unchanged
$DOMAIN {
    encode gzip
    basicauth {
        $RKO_WEB_USER $HASH
    }
    reverse_proxy $GW:$BRIDGE_PORT
}
EOF
docker cp "$TMP" "$C:/tmp/rko-candidate.Caddyfile" || fail 'Не удалось скопировать тестовую конфигурацию в Caddy.'
if ! docker exec "$C" caddy validate --config /tmp/rko-candidate.Caddyfile --adapter caddyfile; then
  rm -f "$TMP"
  fail 'Новая конфигурация не прошла проверку; рабочий Caddyfile не менялся.'
fi
# Preserve host inode: Docker bind-mounts the original Caddyfile itself.
cat "$TMP" > "$FILE"
rm -f "$TMP"
if ! docker exec "$C" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
  echo 'Caddy не принял новую конфигурацию; возвращаю старую.' >&2
  cat "$BACKUP" > "$FILE"
  docker exec "$C" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile || true
  fail 'Публикация RKO отменена; оригинальный конфиг TikTok восстановлен.'
fi
msg '4/4. Настройка выполнена'
echo "Постоянный адрес: https://$DOMAIN"
echo "Дополнительный логин: $RKO_WEB_USER"
echo "Дополнительный пароль: $RKO_WEB_PASS"
echo 'После первого входа браузер также попросит пароль RKO Radar.'
echo "Резервная копия: $BACKUP"
echo 'Если HTTPS недоступен: docker logs tiktok-grev--caddy-1 --tail 50'
