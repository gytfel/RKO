"""Collect from *explicitly configured* public RSS/Atom feeds. No user profiling or outreach."""
import ipaddress
import logging
import socket
import threading
import time
from urllib.parse import urlparse, urljoin, urldefrag

import feedparser
import requests
from bs4 import BeautifulSoup

from db import connect, get_setting, now
from scoring import score_text
from notifications import new_opportunities

log = logging.getLogger(__name__)
USER_AGENT = "RKORadar/1.0 (+self-hosted RSS reader; manual-contact-only)"
MAX_BODY_BYTES = 2_000_000
STATUS = {"running": False, "last_run": None, "added": 0, "checked": 0, "errors": []}
LOCK = threading.Lock()

class FeedError(Exception):
    pass

def validate_public_url(url):
    if len(url) > 2048:
        raise FeedError("Слишком длинный URL")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise FeedError("Разрешены только публичные HTTPS RSS-адреса")
    if parsed.port not in (None, 443):
        raise FeedError("Разрешён только HTTPS-порт 443")
    try:
        ips = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FeedError(f"Не удалось найти сервер: {exc}") from exc
    if not ips or any(not ipaddress.ip_address(item[4][0]).is_global for item in ips):
        raise FeedError("Локальные и закрытые адреса запрещены")
    return url

def fetch_feed(url):
    for _ in range(4):
        validate_public_url(url)
        try:
            with requests.get(url, timeout=(5, 14), headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}, stream=True, allow_redirects=False) as resp:
                if resp.status_code in (301,302,303,307,308):
                    target = resp.headers.get("Location")
                    if not target:
                        raise FeedError("Перенаправление без адреса")
                    url = urljoin(url, target)
                    continue
                resp.raise_for_status()
                pieces, total = [], 0
                for part in resp.iter_content(chunk_size=65536):
                    total += len(part)
                    if total > MAX_BODY_BYTES:
                        raise FeedError("RSS-файл больше 2 МБ")
                    pieces.append(part)
                return b"".join(pieces)
        except requests.RequestException as exc:
            raise FeedError(f"Ошибка загрузки: {str(exc)[:180]}") from exc
    raise FeedError("Слишком много перенаправлений")

def clean_text(value, limit=1300):
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)[:limit]

def normalize_link(link):
    parsed = urlparse(link)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        return None
    # preserve full URL, except fragments, to prevent duplicate entries by fragment
    return urldefrag(link)[0][:2048]

def run_once():
    if not LOCK.acquire(blocking=False):
        return False
    STATUS.update(running=True, added=0, checked=0, errors=[])
    try:
        with connect() as db:
            sources = [dict(row) for row in db.execute("SELECT * FROM sources WHERE enabled=1 ORDER BY id")]
        opportunities = []
        for source in sources:
            error = ""
            try:
                raw = fetch_feed(source["url"])
                feed = feedparser.parse(raw)
                if not feed.entries and feed.bozo:
                    raise FeedError("Ответ не похож на RSS/Atom")
                for entry in feed.entries[:150]:
                    url = normalize_link(entry.get("link", ""))
                    if not url:
                        continue
                    title = clean_text(entry.get("title", ""), 250) or "Без названия"
                    description = clean_text(entry.get("summary", "") or entry.get("description", ""))
                    cls = score_text(title, description)
                    with connect() as db:
                        cur = db.execute("""INSERT OR IGNORE INTO leads
                          (source_id,title,description,url,category,score,reason,created_at,published_at,
                           intent_label,intent_summary,scored_version)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,5)""", (source["id"], title, description, url,
                          cls["category"], cls["score"], cls["reason"], now(), entry.get("published", "")[:120],
                          cls["intent_label"], cls["intent_summary"]))
                        STATUS["added"] += cur.rowcount
                        if cur.rowcount and cls['intent_label'] == 'potential':
                            opportunities.append({'title': title, 'score': cls['score'], 'url': url})
            except Exception as exc:
                error = str(exc)[:250]
                STATUS["errors"].append(f"{source['name']}: {error}")
                log.warning("Source %s: %s", source["name"], error)
            with connect() as db:
                db.execute("UPDATE sources SET checked_at=?, last_error=? WHERE id=?", (now(), error, source["id"]))
            STATUS["checked"] += 1
        # Alerts only for NEW entries; rescoring historic rows cannot flood the chat.
        new_opportunities(opportunities)
    finally:
        STATUS["running"] = False
        STATUS["last_run"] = now()
        LOCK.release()
    return True

def start_refresh():
    if LOCK.locked():
        return False
    threading.Thread(target=run_once, daemon=True, name="refresh-feeds").start()
    return True

def periodic_worker():
    time.sleep(15)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("Periodic source refresh failed")
        try:
            interval = max(15, min(1440, int(get_setting("poll_minutes", "60"))))
        except ValueError:
            interval = 60
        time.sleep(interval * 60)

def start_worker():
    threading.Thread(target=periodic_worker, daemon=True, name="periodic-poller").start()
