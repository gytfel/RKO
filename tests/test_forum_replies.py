"""RSS forum replies with new post-specific links (not old thread root)."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


class RecentReplyTests(unittest.TestCase):
    def test_only_post_anchor_survives(self):
        from collector import normalize_link, reply_permalink
        link = "https://hranidengi.com/threads/topic.25/#post-123456"
        self.assertEqual(normalize_link(link), link)
        self.assertTrue(reply_permalink(link))
        self.assertEqual(normalize_link("https://hranidengi.com/threads/topic.25/#latest"),
                         "https://hranidengi.com/threads/topic.25/")
        self.assertFalse(reply_permalink("https://hranidengi.com/threads/topic.25/"))
        self.assertTrue(reply_permalink("https://hranidengi.com/posts/123456/"))

    def test_new_reply_guid_saved_separately(self):
        import db
        import collector
        self.tmp = tempfile.TemporaryDirectory()
        orig_dir, orig_file = db.DATA_DIR, db.DB_FILE
        db.DATA_DIR = self.tmp.name
        db.DB_FILE = os.path.join(self.tmp.name, "radar.sqlite3")
        try:
            db.initialize()
            now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
            entry = {"title": "Подскажите какую дебетовую карту выбрать?",
                     "summary": "Хочу оформить новую дебетовую карту",
                     "link": "https://example.org/threads/old-topic.25/",
                     "id": "https://example.org/threads/old-topic.25/#post-88281",
                     "published": now}
            class Feed:
                entries = [entry]
                bozo = False
            with db.connect() as cx:
                cx.execute("INSERT INTO sources(name,url,enabled,created_at) VALUES (?,?,1,?)",
                           ("Forum", "https://example.org/forums/-/index.rss", db.now()))
                cx.execute("UPDATE sources SET enabled=0 WHERE name != 'Forum'")
            with patch.object(collector, "fetch_feed", return_value=b"<rss/>"), \
                 patch.object(collector.feedparser, "parse", return_value=Feed()), \
                 patch.object(collector, "new_opportunities"), \
                 patch.object(collector, "ai_configured", return_value=False):
                self.assertTrue(collector.run_once())
            with db.connect() as cx:
                row = cx.execute("SELECT url, published_on FROM leads").fetchone()
                self.assertEqual(row["url"], entry["id"])
                self.assertEqual(row["published_on"], datetime.now(timezone.utc).date().isoformat())
        finally:
            db.DATA_DIR, db.DB_FILE = orig_dir, orig_file
            self.tmp.cleanup()
