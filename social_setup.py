"""Interactive setup for public social source API tokens on the VPS only."""
import getpass
import os
import re
from pathlib import Path

PATH=Path("/etc/rko-radar-social.env")
TOKEN=re.compile(r"^[A-Za-z0-9._-]{10,512}$")
DOMAIN=re.compile(r"^[A-Za-z0-9_.]{2,60}$")


def main():
    if os.geteuid()!=0:
        raise SystemExit("Используй sudo rko-radar social setup")
    values={}
    if PATH.is_file():
        for line in PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k,v=line.split("=",1)
                if k in ("TGSTAT_API_TOKEN","VK_ACCESS_TOKEN","VK_WALL_DOMAINS"):
                    values[k]=v
    print("TGStat: нужен API Search, поиск по публичным Telegram-сообщениям.")
    print("VK: поиск только по стенам открытых сообществ, выбранных вручную.")
    tg=getpass.getpass("TGStat API Search токен [Enter — сохранить прежний/не подключать]: ").strip()
    if tg:
        if not TOKEN.fullmatch(tg):raise SystemExit("Формат токена TGStat не распознан.")
        values["TGSTAT_API_TOKEN"]=tg
    vk=getpass.getpass("VK API токен [Enter — сохранить прежний/не подключать]: ").strip()
    if vk:
        if not TOKEN.fullmatch(vk):raise SystemExit("Формат токена VK не распознан.")
        values["VK_ACCESS_TOKEN"]=vk
    old=values.get("VK_WALL_DOMAINS","")
    raw=input("Публичные VK-группы через запятую [Enter — оставить "+str(len(old.split(",")) if old else 0)+"]: ").strip()
    if raw:
        names=list(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))
        if len(names)>12 or not all(DOMAIN.fullmatch(x) for x in names):
            raise SystemExit("Нужно 1–12 публичных VK-групп: только короткие названия из адреса vk.com/имя.")
        values["VK_WALL_DOMAINS"]=",".join(names)
    if not values.get("TGSTAT_API_TOKEN") and not (values.get("VK_ACCESS_TOKEN") and values.get("VK_WALL_DOMAINS")):
        raise SystemExit("Нет активных источников. Укажи TGStat токен или VK токен и публичные группы.")
    temp=PATH.with_suffix(".env.tmp")
    try:
        with temp.open("w",encoding="utf-8") as f:
            for name in ("TGSTAT_API_TOKEN","VK_ACCESS_TOKEN","VK_WALL_DOMAINS"):
                if values.get(name): f.write(name+"="+values[name]+"\n")
        os.chmod(temp,0o600)
        os.replace(temp,PATH)
        os.chmod(PATH,0o600)
    finally:
        if temp.exists():temp.unlink()
    print("✓ Настройки сохранены на VPS, токены не выводились и не загружались в GitHub.")
    print("Если только один из API доступен, другой можно подключить позже той же командой.")


if __name__=="__main__":
    main()
