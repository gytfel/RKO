"""Offline regression tests for scheduled, budgeted Tavily discovery."""
import os
import tempfile
import unittest
from unittest.mock import patch

class WebDiscoveryTests(unittest.TestCase):
    def setUp(self):
        # Import only after API tests establish an isolated DATA_DIR.
        global db, web_discovery
        import db
        import web_discovery
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir, self.old_file = db.DATA_DIR, db.DB_FILE
        db.DATA_DIR = self.tmp.name
        db.DB_FILE = os.path.join(self.tmp.name, "radar.sqlite3")
        db.initialize()
        web_discovery.STATUS.update(running=False, added=0, checked=0, errors=[], last_run=None)

    def tearDown(self):
        db.DATA_DIR, db.DB_FILE = self.old_dir, self.old_file
        self.tmp.cleanup()

    def test_url_filter(self):
        self.assertIsNone(web_discovery.public_result_url("http://example.com/question"))
        self.assertIsNone(web_discovery.public_result_url("https://localhost/private"))
        self.assertIsNone(web_discovery.public_result_url("https://127.0.0.1/private"))
        self.assertEqual(
            web_discovery.public_result_url("https://example.org/thread?id=42&utm_source=spam#reply"),
            "https://example.org/thread?id=42",
        )

    def test_disabled_without_key(self):
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}):
            self.assertFalse(web_discovery.start_refresh())
            self.assertFalse(web_discovery.status()["configured"])

    def test_saves_public_question_once(self):
        result = {
            "title": "Подскажите, какой банк выбрать для ИП?",
            "url": "https://example.org/forum/thread-42",
            "content": "Хочу открыть расчётный счёт для нового ИП, посоветуйте РКО",
            "published_date": "2026-11-15",
        }
        with patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-test-key"}):
            with patch.object(web_discovery, "_search", return_value=[result]) as search:
                with patch.object(web_discovery, "new_opportunities") as notify:
                    self.assertTrue(web_discovery.run_once())
                    self.assertEqual(search.call_count, web_discovery.QUERIES_PER_RUN)
                    with db.connect() as cx:
                        rows = cx.execute("SELECT * FROM leads WHERE origin='web'").fetchall()
                        self.assertEqual(len(rows), 1)
                        self.assertEqual(rows[0]["intent_label"], "potential")
                    self.assertEqual(web_discovery.status()["used_today"], 5)
                    self.assertTrue(notify.called)

    def test_article_is_not_saved(self):
        news = {
            "title": "Банк объявил об изменении тарифов РКО",
            "url": "https://example.org/news",
            "content": "Новости и обзор тарифов для предпринимателей",
        }
        with patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-test-key"}):
            with patch.object(web_discovery, "_search", return_value=[news]):
                with patch.object(web_discovery, "new_opportunities"):
                    web_discovery.run_once()
                    with db.connect() as cx:
                        self.assertEqual(cx.execute("SELECT COUNT(*) FROM leads").fetchone()[0], 0)

    def test_daily_budget_cap(self):
        with db.connect() as cx:
            web_discovery._put(cx, "web_day", web_discovery._budget_snapshot()[0])
            web_discovery._put(cx, "web_used", web_discovery.DAILY_LIMIT)
        self.assertFalse(web_discovery._consume_credit())


if __name__ == "__main__":
    unittest.main()
