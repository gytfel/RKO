import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
DB_FILE = os.path.join(DATA_DIR, "radar.sqlite3")

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

@contextmanager
def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=20000")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def initialize():
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1,
                checked_at TEXT, last_error TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER,
                title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', url TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT 'new',
                notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                published_at TEXT,
                FOREIGN KEY(source_id) REFERENCES sources(id));
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC);
            CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(stage);
        """)
        # Non-destructive schema migration for existing v3 installations.
        columns = {row[1] for row in db.execute("PRAGMA table_info(leads)")}
        for name, definition in (("intent_label", "TEXT NOT NULL DEFAULT 'information'"),
                                 ("intent_summary", "TEXT NOT NULL DEFAULT ''"),
                                 ("scored_version", "INTEGER NOT NULL DEFAULT 3")):
            if name not in columns:
                db.execute(f"ALTER TABLE leads ADD COLUMN {name} {definition}")
        db.execute("CREATE INDEX IF NOT EXISTS idx_leads_intent ON leads(intent_label, score DESC)")
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('poll_minutes','60')")
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('min_score','35')")
        db.execute("""INSERT OR IGNORE INTO sources(name,url,enabled,created_at)
                      VALUES (?,?,1,?)""", ("Хабр Q&A — новые вопросы", "https://qna.habr.com/rss/questions_latest/", now()))

def get_setting(key, fallback):
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else fallback


def rescore_existing(limit=50000):
    """Upgrade v3 classifications, preserving URLs, notes, source and all work stages."""
    from scoring import score_text
    updated = 0
    with connect() as db:
        rows = db.execute("SELECT id, title, description FROM leads WHERE scored_version < 5 LIMIT ?", (limit,)).fetchall()
        for row in rows:
            value = score_text(row['title'], row['description'])
            db.execute("""UPDATE leads SET category=?, score=?, reason=?, intent_label=?, intent_summary=?, scored_version=5
                         WHERE id=?""", (value['category'], value['score'], value['reason'], value['intent_label'], value['intent_summary'], row['id']))
            updated += 1
    return updated
