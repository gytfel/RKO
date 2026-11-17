"""Private console setup for optional Yandex/Brave/Telemetr search keys.

Credentials remain in /etc/rko-radar-extra.env (0600). Never commit secrets.
"""
import getpass
import os
import re
from pathlib import Path

PATH=Path("/etc/rko-radar-extra.env")
KEYS=("YANDEX_SEARCH_API_KEY","YANDEX_SEARCH_FOLDER_ID",
      "BRAVE_SEARCH_API_KEY","TELEMETR_API_KEY")


def _token(name, old):
    hint="настроен" if old else "не настроен"
    raw=getpass.getpass(name+" ("+hint+", Enter — оставить): ").strip()
    if raw and (len(raw)>1024 or re.search(r"[\s'\";&<>]",raw)):
        raise SystemExit("Недопустимые символы в ключе/идентификаторе.")
    return raw or old


def main():
    if os.geteuid()!=0:
        raise SystemExit("Запускай через sudo rko-radar extra setup")
    values={}
    if PATH.is_file():
        for line in PATH.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key,value=line.split("=",1)
            if key in KEYS:
                values[key]=value
    print("Дополнительный поиск: Bankiros/Банки.ру через Tavily (ключ не нужен),")
    print("Yandex Search API (API-ключ + folder ID), Brave Search API, Telemetr.io.")
    print("Новые источники могут быть платными. Можно настроить только один.")
    for key in KEYS:
        values[key]=_token(key, values.get(key,""))
    if not any([values.get("BRAVE_SEARCH_API_KEY"),values.get("TELEMETR_API_KEY"),
                values.get("YANDEX_SEARCH_API_KEY") and values.get("YANDEX_SEARCH_FOLDER_ID")]):
        raise SystemExit("Не введено ни одного полного набора ключей. Ничего не изменено.")
    tmp=PATH.with_suffix(".env.tmp")
    try:
        with tmp.open("w",encoding="utf-8") as fp:
            for key in KEYS:
                if values[key]:
                    fp.write(key+"="+values[key]+"\n")
        os.chmod(tmp,0o600)
        os.replace(tmp,PATH)
        os.chmod(PATH,0o600)
    finally:
        if tmp.exists(): tmp.unlink()
    print("✓ Настройки сохранены на сервере; ключи не выводятся и не отправляются в GitHub.")


if __name__=="__main__":
    main()
