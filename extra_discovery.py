"""Additional opt-in public discovery: Yandex Search, Brave Search and Telemetr.

- Official API access only; no HTML scraping of protected services.
- Content dates are never replaced with fetched/updated/indexed timestamps.
- Public source URLs, explicit 7-day freshness and local relevance filters.
- Bounded quotas and automatic de-duplication; no messaging or private profiles.
"""
import base64
import binascii
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests

from db import connect, get_setting, now
from freshness import date_status, normalize_published_date, old_year_in_url, window_bounds
from notifications import new_opportunities
from scoring import score_text
from search_catalog import RKO_QUERIES, CARD_QUERIES, REFERRAL_QUERIES
from web_discovery import clean, public_result_url

LOG = logging.getLogger(__name__)
YANDEX_ENDPOINT = "https://searchapi.api.cloud.yandex.net/v2/web/search"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
TELEMETR_ENDPOINT = "https://api.tlmtr.io/v1/search/messages"
INTERVAL_SECONDS = 4 * 3600
DAILY_LIMIT = {"yandex": 6, "brave": 6, "telemetr": 6}
ORIGINS = {"yandex": "yandex", "brave": "brave", "telemetr": "telemetr"}
LOCK = threading.Lock()
STATUS = {"running": False, "checked": 0, "added": 0, "received": 0,
          "filtered": 0, "undated": 0, "stale": 0, "duplicates": 0, "errors": []}
# Shorter queries for paid APIs; alternate RKO, debit card and referral requests.
QUERIES = (
    "посоветуйте банк для ИП открыть расчетный счет",
    "хочу оформить дебетовую карту посоветуйте",
    "ищу реферальную ссылку на дебетовую карту",
    "site:bankiros.ru/questions/ открыть расчетный счет ИП",
    "site:banki.ru/services/questions-answers/question/ выбрать карту",
    "site:bankiros.ru/questions дебетовая карта вопрос",
    "site:banki.ru/dialog/ реферальное приглашение карта",
    "хочу открыть счет для ООО какой банк",
    "какую дебетовую карту лучше открыть",
    "у кого есть приглашение Альфа карта",
)


def yandex_enabled():
    return bool(os.getenv("YANDEX_SEARCH_API_KEY", "").strip() and
                os.getenv("YANDEX_SEARCH_FOLDER_ID", "").strip())


def brave_enabled():
    return bool(os.getenv("BRAVE_SEARCH_API_KEY", "").strip())


def telemetr_enabled():
    return bool(os.getenv("TELEMETR_API_KEY", "").strip())


def configured():
    return yandex_enabled() or brave_enabled() or telemetr_enabled()


def _get(db, name, default=""):
    row = db.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
    return row["value"] if row else default


def _put(db, name, value):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (name, str(value)))


def _used(provider):
    today = datetime.now(timezone.utc).date().isoformat()
    with connect() as db:
        if _get(db, "extra_"+provider+"_day") != today:
            return 0
        return int(_get(db, "extra_"+provider+"_used", "0"))


def _reserve(provider):
    today = datetime.now(timezone.utc).date().isoformat()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        used = (int(_get(db, "extra_"+provider+"_used", "0"))
                if _get(db, "extra_"+provider+"_day") == today else 0)
        if used >= DAILY_LIMIT[provider]:
            return False
        _put(db, "extra_"+provider+"_day", today)
        _put(db, "extra_"+provider+"_used", used+1)
    return True


def status():
    with connect() as db:
        latest = _get(db, "extra_last_run")
        total = db.execute("SELECT COUNT(*) FROM leads WHERE origin IN ('yandex','brave','telemetr')").fetchone()[0]
        history = {k: int(_get(db, "extra_last_"+k, "0"))
                   for k in ("added","received","filtered","undated","stale","duplicates")}
    return {
        "configured": configured(), "running": STATUS["running"], "last_run": latest or None,
        "yandex_configured": yandex_enabled(), "brave_configured": brave_enabled(),
        "telemetr_configured": telemetr_enabled(), "results": total,
        "used": {k: _used(k) for k in DAILY_LIMIT},
        "limits": DAILY_LIMIT, "interval_hours": 4,
        "errors": list(STATUS["errors"])[:3],
        **{k: STATUS[k] if STATUS["running"] else history[k] for k in history}
    }


def _request_yandex(query):
    payload = {
        "query": {"searchType":"SEARCH_TYPE_RU", "queryText":query[:400],
                  "familyMode":"FAMILY_MODE_MODERATE", "page":"0"},
        "sortSpec":{"sortMode":"SORT_MODE_BY_TIME","sortOrder":"SORT_ORDER_DESC"},
        "groupSpec":{"groupMode":"GROUP_MODE_FLAT","groupsOnPage":"10","docsInGroup":"1"},
        "folderId":os.environ["YANDEX_SEARCH_FOLDER_ID"].strip(),
        "responseFormat":"FORMAT_XML", "period":"PERIOD_2_WEEKS"
    }
    resp = requests.post(YANDEX_ENDPOINT, headers={
        "Authorization":"Api-Key "+os.environ["YANDEX_SEARCH_API_KEY"].strip(),
        "Content-Type":"application/json"},
        json=payload, timeout=(5,25))
    if resp.status_code in (401,403):
        raise RuntimeError("Yandex Search: ключ, каталог или права доступа неверны.")
    if resp.status_code in (402,429):
        raise RuntimeError("Yandex Search: тариф или квота ограничены.")
    resp.raise_for_status()
    data = resp.json()
    raw = data.get("rawData")
    if not isinstance(raw,str) or len(raw)>3_000_000:
        raise ValueError("Yandex Search: XML не найден или слишком велик.")
    try:
        xml = base64.b64decode(raw, validate=True)
        if len(xml)>2_000_000:
            raise ValueError("Yandex Search: ответ превышает 2 МБ.")
        root = ElementTree.fromstring(xml)
    except (binascii.Error,ValueError,ElementTree.ParseError) as exc:
        raise ValueError("Yandex Search: некорректный XML/Base64.") from exc
    result=[]
    for doc in root.findall(".//doc")[:20]:
        def field(tag):
            el = doc.find(tag)
            return "".join(el.itertext()).strip() if el is not None else ""
        summary = " ".join("".join(el.itertext()) for el in doc.findall(".//passage"))
        # "modtime" means last modification, NOT original publication.
        # Some documents provide an explicit publication date; otherwise skip
        # the result downstream instead of claiming it is a fresh request.
        publication=field("pubdate") or field("published")
        result.append({"url":field("url"),"title":field("title"),
                       "description":summary,"published":publication})
    return result


def _brave_pub_date(row):
    """Only explicit schema.org datePublished; page_age can be last-modified."""
    for item in row.get("schemas") or []:
        if not isinstance(item,dict):
            continue
        for key in ("datePublished", "date_published", "published_at"):
            if item.get(key):
                return str(item[key])
        for child_key in ("article","creative_work","question","discussion"):
            child=item.get(child_key)
            if isinstance(child,dict) and child.get("datePublished"):
                return str(child["datePublished"])
    return ""


def _request_brave(query):
    resp = requests.get(BRAVE_ENDPOINT,headers={
        "X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"].strip(),
        "Accept":"application/json"}, params={
            "q":query,"count":10,"freshness":"pw","search_lang":"ru",
            "country":"RU","result_filter":"web,discussions",
            "text_decorations":"false"
        },timeout=(5,20))
    if resp.status_code in (401,403):
        raise RuntimeError("Brave: проверь ключ Search API.")
    if resp.status_code in (402,429):
        raise RuntimeError("Brave: лимит тарифного плана исчерпан.")
    resp.raise_for_status()
    data=resp.json()
    rows=[]
    for section in ("web","discussions"):
        block = data.get(section) or {}
        for item in block.get("results",[])[:12]:
            if not isinstance(item,dict):
                continue
            rows.append({"url":item.get("url"),"title":item.get("title"),
                         "description":item.get("description") or item.get("snippet"),
                         "published":_brave_pub_date(item)})
    return rows


def _public_telemetr_link(msg, chats):
    raw = msg.get("link") or ""
    if raw:
        parsed=public_result_url(str(raw))
        if parsed and re.fullmatch(r"https://t\.me/[A-Za-z_][A-Za-z_0-9]{3,31}/\d+",parsed):
            return parsed
    key = str(msg.get("peer_id") or "")
    chat=chats.get(key) or {}
    username=str(chat.get("username") or "").lstrip("@")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{3,31}",username):
        return ""
    try:
        number = int(msg.get("message_id"))
    except (ValueError,TypeError):
        return ""
    return "https://t.me/"+username+"/"+str(number) if number>0 else ""


def _request_telemetr(query):
    cutoff,end=window_bounds()
    resp=requests.get(TELEMETR_ENDPOINT,headers={
        "x-api-key":os.environ["TELEMETR_API_KEY"].strip(),
        "Accept":"application/json"
    },params={"term":query,"date_from":cutoff,"date_to":end,
              "sort":"date","return_short_info":"true"},
        timeout=(5,25))
    if resp.status_code in (401,403):
        raise RuntimeError("Telemetr: нет доступа к поиску публикаций или ключ неверный.")
    if resp.status_code in (402,429):
        raise RuntimeError("Telemetr: квота/кредиты исчерпаны.")
    resp.raise_for_status()
    data=resp.json()
    chats={str(c.get("internal_id")):c for c in (data.get("chats") or [])
           if isinstance(c,dict)}
    items=[]
    for msg in (data.get("messages") or [])[:40]:
        if not isinstance(msg,dict):
            continue
        url=_public_telemetr_link(msg,chats)
        if not url:
            continue
        txt=clean(msg.get("text"),1300)
        if not txt:
            continue
        items.append({"url":url, "title":txt[:250], "description":txt,
                      "published":msg.get("date") or ""})
    return items


def _save(provider, entry):
    url=public_result_url(entry.get("url"))
    if not url:
        STATUS["filtered"]+=1
        return None
    original=str(entry.get("published") or "")[:120]
    kind=date_status(original)
    if kind!="fresh" or old_year_in_url(url):
        STATUS["filtered"]+=1
        STATUS["stale" if kind=="stale" or old_year_in_url(url) else "undated"]+=1
        return None
    title=clean(entry.get("title"),250)
    description=clean(entry.get("description"),1300)
    if not title:
        STATUS["filtered"]+=1
        return None
    cls=score_text(title,description)
    if cls["category"]=="Другое" or cls["intent_label"]=="information":
        STATUS["filtered"]+=1
        return None
    with connect() as db:
        row=db.execute("""INSERT OR IGNORE INTO leads
            (source_id,title,description,url,category,score,reason,created_at,
             published_at,published_on,intent_label,intent_summary,scored_version,origin,referral_signal)
            VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (title,description,url,cls["category"],cls["score"],cls["reason"],now(),
             original,normalize_published_date(original),
             cls["intent_label"],cls["intent_summary"],7,provider,cls["referral_signal"]))
        if not row.rowcount:
            STATUS["duplicates"]+=1
            return None
        STATUS["added"]+=1
    if cls["intent_label"]=="potential":
        return {"title":title,"score":cls["score"],"url":url}
    return None


def run_once():
    if not configured() or not LOCK.acquire(blocking=False):
        return False
    STATUS.update(running=True, checked=0, added=0, received=0, filtered=0,
                  undated=0, stale=0, duplicates=0, errors=[])
    found=[]
    try:
        with connect() as db:
            cursor=int(_get(db,"extra_cursor","0"))
        for provider,enabled,search in (
            ("yandex",yandex_enabled,_request_yandex),
            ("brave",brave_enabled,_request_brave),
            ("telemetr",telemetr_enabled,_request_telemetr),
        ):
            if not enabled():
                continue
            if not _reserve(provider):
                STATUS["errors"].append(provider+": дневной лимит API достигнут.")
                continue
            try:
                result=search(QUERIES[cursor%len(QUERIES)])
                STATUS["received"]+=len(result)
                for entry in result:
                    candidate=_save(provider,entry)
                    if candidate:
                        found.append(candidate)
            except (requests.RequestException,ValueError,TypeError,RuntimeError,
                    ElementTree.ParseError) as exc:
                LOG.warning("External provider %s failed (%s)",provider,type(exc).__name__)
                STATUS["errors"].append(
                    str(exc)[:160] if isinstance(exc,RuntimeError) else provider+": ошибка ответа API."
                )
            STATUS["checked"]+=1
        with connect() as db:
            _put(db,"extra_cursor",cursor+1)
            _put(db,"extra_last_run",now())
            for name in ("added","received","filtered","stale","undated","duplicates"):
                _put(db,"extra_last_"+name,STATUS[name])
        new_opportunities(found[:5])
    finally:
        STATUS["running"]=False
        LOCK.release()
    return True


def start_refresh():
    if not configured() or LOCK.locked():
        return False
    threading.Thread(target=run_once,daemon=True,name="extra-public-discovery").start()
    return True


def periodic_worker():
    time.sleep(75)
    while True:
        try:
            stamp=get_setting("extra_last_run","")
            if not stamp or (datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds()>=INTERVAL_SECONDS:
                run_once()
        except Exception:
            LOG.exception("Extra provider worker failed")
        time.sleep(600)


def start_worker():
    if configured():
        threading.Thread(target=periodic_worker,daemon=True,name="extra-public-scheduler").start()
