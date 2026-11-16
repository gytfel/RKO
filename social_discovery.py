"""Opt-in public social monitoring using provider APIs (no scraping or messages).

TGStat API Search: recent public Telegram channel/chat messages (subscription needed).
VK wall.search: posts on explicitly configured public community walls, NOT global
VK search and NOT private chats/comments.
"""
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from db import connect, now, get_setting
from freshness import normalize_published_date, fresh_publication, window_bounds
from notifications import new_opportunities
from scoring import score_text
from search_catalog import CARD_QUERIES, REFERRAL_QUERIES, RKO_QUERIES
from web_discovery import clean, public_result_url

LOG = logging.getLogger(__name__)
TG_ENDPOINT = "https://api.tgstat.ru/posts/search"
VK_ENDPOINT = "https://api.vk.com/method/wall.search"
TG_DAILY_LIMIT = 18
VK_DAILY_LIMIT = 30
CHECK_INTERVAL_SECONDS = 60 * 60
STATUS = {"running": False, "checked": 0, "added": 0, "received": 0,
          "filtered": 0, "duplicates": 0, "errors": []}
LOCK = threading.Lock()
DOMAIN_RE = re.compile(r"^[A-Za-z0-9_.]{2,60}$")
# Short phrases instead of SEO-article search syntax.
PHRASES = (
    "посоветуйте банк для ип",
    "какую дебетовую карту",
    "ищу реферальную ссылку",
    "нужна дебетовка",
    "открыть расчетный счет",
    "карту по приглашению",
    "приглашение альфа банк",
    "хочу оформить карту",
    "какой банк для ооо",
)


def vk_communities():
    return [domain for domain in
            [x.strip() for x in os.getenv("VK_WALL_DOMAINS", "").split(",")]
            if DOMAIN_RE.fullmatch(domain)][:12]


def configured():
    return bool(tg_enabled() or vk_enabled())


def tg_enabled():
    return bool(os.getenv("TGSTAT_API_TOKEN", "").strip())


def vk_enabled():
    return bool(os.getenv("VK_ACCESS_TOKEN", "").strip() and vk_communities())


def _setting(db, key, default=""):
    r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def _put(db, key, val):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, str(val)))


def _reserve(prefix, limit):
    today = datetime.now(timezone.utc).date().isoformat()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        used = int(_setting(db, prefix+"_used", "0")) if _setting(db, prefix+"_day") == today else 0
        if used >= limit:
            return False
        _put(db, prefix+"_day", today)
        _put(db, prefix+"_used", used+1)
    return True


def _used(prefix):
    today = datetime.now(timezone.utc).date().isoformat()
    with connect() as db:
        return int(_setting(db, prefix+"_used", "0")) if _setting(db, prefix+"_day") == today else 0


def status():
    with connect() as db:
        last = _setting(db, "social_last_run")
        count = db.execute("SELECT COUNT(*) FROM leads WHERE origin IN ('tgstat','vk')").fetchone()[0]
        saved = {k:int(_setting(db, "social_last_"+k, "0"))
                 for k in ("added", "received", "filtered", "duplicates")}
    return {"configured":configured(), "tgstat_configured":tg_enabled(), "vk_configured":vk_enabled(),
            "communities":len(vk_communities()), "running":STATUS["running"],
            "last_run":last or None, "results":count,
            "added":STATUS["added"] if STATUS["running"] else saved["added"],
            "received":STATUS["received"] if STATUS["running"] else saved["received"],
            "filtered":STATUS["filtered"] if STATUS["running"] else saved["filtered"],
            "duplicates":STATUS["duplicates"] if STATUS["running"] else saved["duplicates"],
            "tg_used":_used("social_tg"), "tg_limit":TG_DAILY_LIMIT,
            "vk_used":_used("social_vk"), "vk_limit":VK_DAILY_LIMIT,
            "errors":list(STATUS["errors"])[:3], "interval_hours":1}


def _tg_search(query):
    resp = requests.get(TG_ENDPOINT, params={
        "token":os.environ["TGSTAT_API_TOKEN"].strip(),
        "q":query, "limit":20, "peerType":"all",
        "startDate":int((datetime.now(timezone.utc)-timedelta(days=7)).timestamp()),
        "hideForwards":1, "hideDeleted":1,
    },timeout=(5,16))
    if resp.status_code in (401,403):
        raise RuntimeError("TGStat: проверь токен и тариф API Search.")
    if resp.status_code in (402,429):
        raise RuntimeError("TGStat: достигнут лимит или отсутствует доступ.")
    if not resp.ok:
        raise RuntimeError("TGStat: ошибка HTTP "+str(resp.status_code))
    body=resp.json()
    if body.get("status")!="ok":
        raise RuntimeError("TGStat: API вернул ошибку. Проверь тариф API Search.")
    return body.get("response",{}).get("items",[])


def _vk_search(domain, query):
    resp=requests.post(VK_ENDPOINT,data={
        "access_token":os.environ["VK_ACCESS_TOKEN"].strip(),
        "v":"5.131", "domain":domain,"query":query,
        "count":25,"owners_only":0,"extended":0,
    },timeout=(5,16))
    if not resp.ok:
        raise RuntimeError("VK: ошибка HTTP "+str(resp.status_code))
    body=resp.json()
    if "error" in body:
        code=body["error"].get("error_code")
        raise RuntimeError("VK API: ошибка "+str(code)+"; проверь токен и права.")
    return body.get("response",{}).get("items",[])


def _tg_candidate(post):
    if post.get("forwarded_from") or post.get("is_deleted"):
        return None
    raw_link = str(post.get("link") or "")
    if not raw_link.startswith(("https://t.me/","t.me/")):
        return None
    link=public_result_url(raw_link if raw_link.startswith("https://") else "https://"+raw_link)
    if not link or not re.fullmatch(r"https://t\.me/[A-Za-z_][A-Za-z_0-9]{3,31}/\d+",link):
        return None
    text=clean(post.get("text") or "",1800)
    try:
        date=datetime.fromtimestamp(int(post["date"]),timezone.utc)
    except (ValueError,TypeError,OverflowError,KeyError):
        return None
    if not text or not fresh_publication(date, link):
        return None
    return {"url":link,"title":text[:230].split("\n")[0],
            "description":text[:1300],"published":date.isoformat(),"origin":"tgstat"}


def _vk_candidate(post):
    try:
        owner=int(post["owner_id"])
        identifier=int(post["id"])
        created=datetime.fromtimestamp(int(post["date"]),timezone.utc)
    except (ValueError,TypeError,OverflowError,KeyError):
        return None
    if identifier<=0 or owner==0:
        return None
    link="https://vk.com/wall"+str(owner)+"_"+str(identifier)
    content=clean(post.get("text") or "",1800)
    if not content or not fresh_publication(created,link):
        return None
    # Never collect author profiles, IDs of repliers, phone numbers or contacts.
    return {"url":link,"title":content[:230].split("\n")[0],
            "description":content[:1300], "published":created.isoformat(),"origin":"vk"}


def _save(candidate):
    cls=score_text(candidate["title"],candidate["description"])
    if cls["category"]=="Другое" or cls["intent_label"]=="information":
        STATUS["filtered"]+=1
        return None
    with connect() as db:
        cur=db.execute(
            """INSERT OR IGNORE INTO leads
               (source_id,title,description,url,category,score,reason,created_at,
                published_at,published_on,intent_label,intent_summary,scored_version,
                origin,referral_signal)
               VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (candidate["title"],candidate["description"],candidate["url"],
             cls["category"],cls["score"],cls["reason"],now(),
             candidate["published"],normalize_published_date(candidate["published"]),
             cls["intent_label"],cls["intent_summary"],7,candidate["origin"],cls["referral_signal"]),
        )
        if not cur.rowcount:
            STATUS["duplicates"]+=1
            return None
        STATUS["added"]+=1
    return {"title":candidate["title"],"url":candidate["url"],"score":cls["score"]} if cls["intent_label"]=="potential" else None


def run_once():
    if not configured() or not LOCK.acquire(blocking=False):
        return False
    STATUS.update(running=True,checked=0,added=0,received=0,filtered=0,duplicates=0,errors=[])
    notify=[]
    try:
        with connect() as db:
            cursor=int(_setting(db,"social_cursor","0"))
        if tg_enabled():
            for i in range(3):
                if not _reserve("social_tg",TG_DAILY_LIMIT):
                    STATUS["errors"].append("TGStat: дневной лимит запросов.")
                    break
                try:
                    results=_tg_search(PHRASES[(cursor+i)%len(PHRASES)])
                    STATUS["received"]+=len(results)
                    for item in results:
                        candidate=_tg_candidate(item)
                        if candidate:
                            found=_save(candidate)
                            if found: notify.append(found)
                        else: STATUS["filtered"]+=1
                except (ValueError,TypeError,RuntimeError,requests.RequestException) as exc:
                    STATUS["errors"].append(str(exc) if isinstance(exc,RuntimeError) else "TGStat: ошибка API/формата.")
                    LOG.warning("Social Telegram search failed (%s)",type(exc).__name__)
                STATUS["checked"]+=1
        if vk_enabled():
            walls=vk_communities()
            # Explicitly selected communities only, no global VK harvesting.
            for i in range(min(3,len(walls))):
                if not _reserve("social_vk",VK_DAILY_LIMIT):
                    STATUS["errors"].append("VK: дневной лимит запросов.")
                    break
                try:
                    domain=walls[(cursor+i)%len(walls)]
                    query=PHRASES[(cursor+i)%len(PHRASES)]
                    results=_vk_search(domain,query)
                    STATUS["received"]+=len(results)
                    for post in results:
                        candidate=_vk_candidate(post)
                        if candidate:
                            found=_save(candidate)
                            if found: notify.append(found)
                        else: STATUS["filtered"]+=1
                except (ValueError,TypeError,RuntimeError,requests.RequestException) as exc:
                    STATUS["errors"].append(str(exc) if isinstance(exc,RuntimeError) else "VK: ошибка API/формата.")
                    LOG.warning("Social VK search failed (%s)",type(exc).__name__)
                STATUS["checked"]+=1
        with connect() as db:
            _put(db,"social_cursor",cursor+1)
            _put(db,"social_last_run",now())
            for field in ("added","received","filtered","duplicates"):
                _put(db,"social_last_"+field,STATUS[field])
        new_opportunities(notify[:5])
    finally:
        STATUS["running"]=False
        LOCK.release()
    return True


def start_refresh():
    if not configured() or LOCK.locked():
        return False
    threading.Thread(target=run_once,name="social-monitor",daemon=True).start()
    return True


def periodic_worker():
    time.sleep(90)
    while True:
        try:
            stamp=get_setting("social_last_run","")
            due=not stamp or (datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds()>=CHECK_INTERVAL_SECONDS
            if due: run_once()
        except Exception:
            LOG.exception("Social discovery scheduler failed")
        time.sleep(600)


def start_worker():
    if configured():
        threading.Thread(target=periodic_worker,name="social-monitor-scheduler",daemon=True).start()
