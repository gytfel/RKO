"""Interactive, server-local Tavily API key setup for RKO Radar."""
import getpass
import os
import re
from pathlib import Path

DEST = Path("/etc/rko-radar-search.env")

def main():
    if os.geteuid() != 0:
        raise SystemExit("Запусти с правами root: sudo rko-radar web setup")
    print("Tavily: https://app.tavily.com/ (бесплатные кредиты, без банковской карты)")
    key = getpass.getpass("Вставь API-ключ Tavily (ввод скрыт): ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,200}", key):
        raise SystemExit("Неверный формат ключа. Файл настроек не изменён.")
    tmp = DEST.with_name(DEST.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write("TAVILY_API_KEY=" + key + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, DEST)
        os.chmod(DEST, 0o600)
    finally:
        if tmp.exists():
            tmp.unlink()
    print("Ключ сохранён только на VPS. Не размещай его в GitHub и не присылай в чат.")
    print("При следующем перезапуске службы поиск активируется.")

if __name__ == "__main__":
    main()
