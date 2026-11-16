"""Publication-date validation for *fresh* lead shortlists.

Never use first-seen / indexing / collection time as the publication date.
An unknown source date is intentionally NOT considered a fresh lead.
"""
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urlsplit

FRESH_DAYS = 7


def normalize_published_date(value, *, today=None):
    """Return UTC YYYY-MM-DD only when a parseable publication date exists."""
    if not value:
        return ""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        item = dt.astimezone(timezone.utc).date()
    elif isinstance(value, date):
        item = value
    else:
        raw = str(value).strip()[:128]
        if not raw:
            return ""
        try:
            # ISO date / RFC3339. Normalize offset-aware times to UTC.
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            item = (dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).date()
        except ValueError:
            try:
                dt = parsedate_to_datetime(raw)
                item = (dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).date()
            except (ValueError, TypeError, IndexError, OverflowError):
                return ""
    today = today or datetime.now(timezone.utc).date()
    # Ignore clearly bogus future timestamps; they cannot prove freshness.
    if item > today or item.year < 2000:
        return ""
    return item.isoformat()


def window_bounds(*, today=None, days=FRESH_DAYS):
    today = today or datetime.now(timezone.utc).date()
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


def date_status(value, *, today=None, days=FRESH_DAYS):
    """Return 'fresh', 'stale', or 'unknown' for a source publication date."""
    normalized = normalize_published_date(value, today=today)
    if not normalized:
        return "unknown"
    cutoff, _ = window_bounds(today=today, days=days)
    return "fresh" if normalized >= cutoff else "stale"


def old_year_in_url(url, *, today=None, days=FRESH_DAYS):
    """Conservatively rule out articles explicitly in an old year archive.

    Don't infer dates from forum IDs or user handles.
    """
    today = today or datetime.now(timezone.utc).date()
    cutoff, _ = window_bounds(today=today, days=days)
    try:
        path = urlsplit(url).path
    except ValueError:
        return False
    for match in re.finditer(r"(?:^|/)(20\d{2})(?=/|[-_])", path):
        year = int(match.group(1))
        if 2000 <= year < int(cutoff[:4]):
            return True
    return False


def fresh_publication(value, url="", *, today=None):
    return date_status(value, today=today) == "fresh" and not old_year_in_url(url, today=today)
