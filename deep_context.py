"""Opt-in full-page context via Tavily Extract for bounded DeepSeek reviews.

Default OFF: additional API credits; never downloads URLs directly from VPS.
Only public allowlisted site URLs, no private group/forum login, no user profiles.
No raw full-page contents persisted to database.
"""
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from db import connect
from web_discovery import public_result_url

LOG=logging.getLogger(__name__)
ENDPOINT="https://api.tavily.com/extract"
DAILY_LIMIT=8
ALLOWED_HOSTS=(
    "hranidengi.com","banki.ru","vc.ru","qna.habr.com",
    "pikabu.ru","otvet.mail.ru","habr.com",
)


def enabled():
    return (os.environ.get("DEEP_CONTEXT","0")=="1"
            and bool(os.environ.get("TAVILY_API_KEY","").strip())
            and bool(os.environ.get("DEEPSEEK_API_KEY","").strip())
            and os.environ.get("DEEPSEEK_ANALYZE","0")=="1")


def safe_context_url(url):
    url=public_result_url(url)
    if not url:
        return None
    host=urlsplit(url).hostname or ""
    if not any(host==allowed or host.endswith("."+allowed)
               for allowed in ALLOWED_HOSTS):
        return None
    # Avoid user profile, private-account paths or login-based routes.
    if re.search(r"/(?:account|login|profile|members|users|private)(?:/|$)",urlsplit(url).path,re.I):
        return None
    return url


def _reserve():
    today=datetime.now(timezone.utc).date().isoformat()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        day=db.execute("SELECT value FROM settings WHERE key='context_day'").fetchone()
        used=db.execute("SELECT value FROM settings WHERE key='context_used'").fetchone()
        amount=int(used[0]) if used and day and day[0]==today else 0
        if amount>=DAILY_LIMIT:
            return False
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('context_day',?)",(today,))
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('context_used',?)",(str(amount+1),))
    return True


def usage():
    today=datetime.now(timezone.utc).date().isoformat()
    with connect() as db:
        day=db.execute("SELECT value FROM settings WHERE key='context_day'").fetchone()
        used=db.execute("SELECT value FROM settings WHERE key='context_used'").fetchone()
    return int(used[0]) if used and day and day[0]==today else 0


def status():
    return {"enabled":enabled(),"used_today":usage(),"daily_limit":DAILY_LIMIT,
            "note":"Only whitelisted public discussion pages; no private message collection."}


def extract(url):
    """Return limited, redacted public text, or empty string on provider failure."""
    target=safe_context_url(url)
    if not enabled() or not target or not _reserve():
        return ""
    try:
        response=requests.post(ENDPOINT,headers={
            "Authorization":"Bearer "+os.environ["TAVILY_API_KEY"].strip(),
            "Content-Type":"application/json",
        },json={"urls":[target],"extract_depth":"basic","include_images":False},
            timeout=(5,20))
        if not response.ok:
            LOG.warning("Tavily Extract status %d",response.status_code)
            return ""
        payload=response.json()
        results=payload.get("results") or []
        if not isinstance(results,list) or not results:
            return ""
        raw=results[0].get("raw_content") or ""
        if not isinstance(raw,str):
            return ""
        content=BeautifulSoup(raw[:15000],"html.parser").get_text(" ",strip=True)
        # Do not send unrelated public email/phone details to a third-party LLM.
        content=re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b","[email]",content)
        content=re.sub(r"(?<!\w)(?:\+7|8)[\s()\-]*\d[\s()\-\d]{8,16}(?!\w)","[телефон]",content)
        return content[:2800]
    except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
        LOG.warning("Tavily Extract failed (%s)",type(exc).__name__)
        return ""
