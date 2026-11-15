"""Configure DeepSeek API on the VPS; never print or commit the key."""
import getpass
import os
import re
from pathlib import Path

DEST = Path("/etc/rko-radar-deepseek.env")


def main():
    if os.geteuid() != 0:
        raise SystemExit("Запусти: sudo rko-radar deepseek setup")
    print("Получить ключ: https://platform.deepseek.com/api_keys")
    print("DeepSeek API оплачивается отдельно от ChatGPT. Нужен баланс API.")
    key = getpass.getpass("DeepSeek API key (ввод скрыт): ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", key):
        raise SystemExit("Некорректный формат API-ключа. Настройки не изменены.")
    option = input("Включить платный AI-анализ? [y/N]: ").strip().lower()
    analyze = "1" if option in ("y", "yes", "д", "да") else "0"
    temp = DEST.with_name(DEST.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as out:
            out.write("DEEPSEEK_API_KEY=" + key + "\nDEEPSEEK_ANALYZE=" + analyze + "\n")
        os.chmod(temp, 0o600)
        os.replace(temp, DEST)
        os.chmod(DEST, 0o600)
    finally:
        if temp.exists():
            temp.unlink()
    print("API-ключ сохранён только на VPS. Не публикуй его в GitHub и не присылай в чат.")


if __name__ == "__main__":
    main()
