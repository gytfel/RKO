#!/usr/bin/env bash
# Commands to manage the locally installed RKO Radar service.
set -Eeuo pipefail
ENV_FILE=/etc/rko-radar.env
APP_DIR=/opt/rko-radar
DATA_DIR=/var/lib/rko-radar
SERVICE=rko-radar.service
if [[ ${EUID} -ne 0 ]] && [[ ${1:-help} != port ]] && [[ ${1:-help} != help ]]; then
  if command -v sudo >/dev/null 2>&1; then exec sudo "$0" "$@"; fi
  echo 'Нужны права root.' >&2; exit 1
fi
case "${1:-help}" in
  status) systemctl status "$SERVICE" --no-pager --lines=20 ;;
  restart) systemctl restart "$SERVICE" && echo '✓ Программа перезапущена' ;;
  start) systemctl start "$SERVICE" && echo '✓ Программа запущена' ;;
  stop) systemctl stop "$SERVICE" && echo '✓ Программа остановлена' ;;
  logs) journalctl -u "$SERVICE" -n 100 --no-pager ;;
  port) [[ -f "$APP_DIR/PORT" ]] && cat "$APP_DIR/PORT" || { echo 'Программа ещё не установлена'; exit 1; } ;;
  password)
    [[ -f "$ENV_FILE" ]] || { echo 'Панель ещё не установлена'; exit 1; }
    sed -n 's/^ADMIN_PASSWORD=//p' "$ENV_FILE"
    ;;
  doctor)
    echo 'Проверка службы:'
    systemctl is-active "$SERVICE" || true
    PORT="$(cat "$APP_DIR/PORT" 2>/dev/null || true)"
    if [[ "$PORT" =~ ^[0-9]+$ ]]; then
      echo "Локальный порт: $PORT"
      python3 - "$PORT" <<'PY'
from urllib.request import urlopen
import sys
try:
    with urlopen('http://127.0.0.1:' + sys.argv[1] + '/login', timeout=5) as r:
        print('✓ Веб-панель отвечает, HTTP', r.status)
except Exception as e:
    print('✗ Панель не отвечает:', str(e))
    sys.exit(1)
PY
    fi
    ;;
  telegram)
    case "${2:-status}" in
      setup)
        python3 "$APP_DIR/telegram_setup.py"
        systemctl restart "$SERVICE"
        echo '✓ Telegram настроен. Проверка: раздел «Настройки» в веб-панели.'
        ;;
      status)
        if [[ -s /etc/rko-radar-notify.env ]]; then echo 'Telegram подключён'; else echo 'Telegram не подключён'; fi
        ;;
      disable)
        rm -f /etc/rko-radar-notify.env
        systemctl restart "$SERVICE"
        echo '✓ Уведомления отключены'
        ;;
      *) echo 'Команды: rko-radar telegram setup | status | disable' ;;
    esac
    ;;
  web)
    case "${2:-status}" in
      setup)
        python3 "$APP_DIR/web_setup.py"
        systemctl restart "$SERVICE"
        echo '✓ Поиск по интернету подключён. Запусти поиск из панели RKO Radar.'
        ;;
      status)
        if [[ -s /etc/rko-radar-search.env ]]; then
          echo 'Tavily API-ключ настроен (не отображается).'
        else
          echo 'Tavily не подключён. Команда: sudo rko-radar web setup'
        fi
        ;;
      disable)
        rm -f /etc/rko-radar-search.env
        systemctl restart "$SERVICE"
        echo '✓ Интернет-поиск отключён. Сохранённые публикации остаются.'
        ;;
      *) echo 'Команды: rko-radar web setup | status | disable' ;;
    esac
    ;;
  deepseek)
    case "${2:-status}" in
      setup)
        python3 "$APP_DIR/deepseek_setup.py"
        systemctl restart "$SERVICE"
        echo '✓ DeepSeek подключён; новые результаты будут анализироваться ИИ.'
        ;;
      status)
        if [[ -s /etc/rko-radar-deepseek.env ]]; then
          echo 'DeepSeek API-ключ настроен (не показывается).'
        else
          echo 'DeepSeek не подключён. Запусти: sudo rko-radar deepseek setup'
        fi
        ;;
      disable)
        rm -f /etc/rko-radar-deepseek.env
        systemctl restart "$SERVICE"
        echo '✓ DeepSeek отключён, сохранённые результаты не удалены.'
        ;;
      *) echo 'Команды: rko-radar deepseek setup | status | disable' ;;
    esac
    ;;
  social)
    case "${2:-status}" in
      setup)
        python3 "$APP_DIR/social_setup.py"
        systemctl restart "$SERVICE"
        echo '✓ Публичные источники Telegram/VK подключены.'
        ;;
      status)
        [[ ! -s /etc/rko-radar-social.env ]] && echo 'Социальные API не настроены. Команда: sudo rko-radar social setup' || echo 'Настройки социальных API есть (токены не показываются).'
        ;;
      disable)
        rm -f /etc/rko-radar-social.env
        systemctl restart "$SERVICE"
        echo '✓ Социальный мониторинг отключён, публикации сохранены.'
        ;;
      *) echo 'Команды: rko-radar social setup | status | disable' ;;
    esac
    ;;
  upgrade)
    updater="$APP_DIR/../rko-radar-source/scripts/update-vps.sh"
    # Fetch latest updater before pulling app code (first run included).
    if command -v curl >/dev/null 2>&1; then
      tmp="$(mktemp)"
      trap 'rm -f "$tmp"' EXIT
      curl -fsSL "https://raw.githubusercontent.com/gytfel/RKO/main/scripts/update-vps.sh" -o "$tmp" || { echo 'Не удалось загрузить обновление GitHub.' >&2; exit 1; }
      bash "$tmp"
    elif [[ -f "$updater" ]]; then
      bash "$updater"
    else
      echo 'Нужен curl (apt install curl).' >&2; exit 1
    fi
    ;;
  update)
    [[ -f "${2:-}" ]] || { echo 'Укажи локальный файл обновления, например: rko-radar update /tmp/rko-update-v4.sh' >&2; exit 1; }
    bash "$2"
    ;;
  backup)
    [[ -d "$DATA_DIR" ]] || { echo 'Данные ещё не созданы' >&2; exit 1; }
    backup="/root/rko-radar-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    systemctl stop "$SERVICE"
    trap 'systemctl start "$SERVICE"' EXIT
    tar -C /var/lib -czf "$backup" rko-radar
    chmod 600 "$backup"
    echo "✓ Резервная копия базы: $backup"
    ;;
  help|*)
    cat <<'TXT'
RKO Radar — команды управления на VPS:
  rko-radar status    — работает ли служба
  rko-radar doctor    — проверить доступность панели
  rko-radar logs      — показать журнал
  rko-radar restart   — перезапуск
  rko-radar password  — показать пароль панели (только на VPS)
  rko-radar backup    — создать резервную копию базы
  rko-radar port      — порт панели
  rko-radar telegram setup   — личные Telegram-уведомления
  rko-radar telegram status  — проверить Telegram
  rko-radar telegram disable — отключить уведомления
  rko-radar web setup / status / disable — интернет-поиск через Tavily
  rko-radar deepseek setup / status / disable — поиск и анализ DeepSeek
  rko-radar social setup / status / disable — поиск Telegram TGStat и VK сообществ
  rko-radar upgrade   — обновить с GitHub (без загрузки файлов вручную)
  rko-radar update /путь/к/файлу.sh — установить обновление
TXT
    ;;
esac
