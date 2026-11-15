"""Offline DeepSeek API integration tests. No real API key or internet used."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        global db, ds
        import db
        import deepseek_discovery as ds
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_dir, self.orig_file = db.DATA_DIR, db.DB_FILE
        db.DATA_DIR = self.tmp.name
        db.DB_FILE = os.path.join(self.tmp.name, "radar.sqlite3")
        db.initialize()
        ds.STATUS.update(running=False, checked=0, added=0, reviewed=0, last_run=None, errors=[])

    def tearDown(self):
        db.DATA_DIR, db.DB_FILE = self.orig_dir, self.orig_file
        self.tmp.cleanup()

    def test_without_key_no_search(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            self.assertFalse(ds.configured())
            self.assertFalse(ds.start_refresh())

    def test_response_schema(self):
        answer = {"verdict": "potential", "category": "РКО", "confidence": 92,
                  "reason": "Автор сам спрашивает о счёте ИП", "reply": "Уточните обороты."}
        with patch.object(ds, "_post", return_value={"choices": [{"message": {"content": json.dumps(answer)}}]}) as post:
            v = ds._analyze("Где открыть РКО?", "Хочу выбрать банк")
            self.assertEqual(v["verdict"], "potential")
            self.assertEqual(v["confidence"], 92)
            self.assertTrue(post.call_args.args[1]["safety_check"]["level"] == "high")
            self.assertEqual(post.call_args.args[1]["model"], "deepseek-flash")

    def test_search_save_and_ai_classify(self):
        item = {
            "title": "Подскажите, какой банк выбрать для ИП?",
            "url": "https://example.org/t/12?utm_source=news",
            "summary": "Хочу открыть расчётный счёт для нового ИП. Посоветуйте РКО.",
            "date": "2026-11-15T10:00:00+00:00",
        }
        ai = {"verdict": "potential", "category": "РКО", "confidence": 86,
              "reason": "Личный вопрос", "reply": "Проверьте тарифы и расходы."}
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-deepseek-key", "DEEPSEEK_ANALYZE": "1"}), \
             patch.object(ds, "_search", return_value=[item]) as search, \
             patch.object(ds, "_analyze", return_value=ai) as analyze, \
             patch.object(ds, "new_opportunities") as notify:
            self.assertTrue(ds.run_once())
            self.assertEqual(search.call_count, ds.QUERIES_PER_RUN)
            self.assertEqual(analyze.call_count, 1)
            with db.connect() as cx:
                rows = cx.execute("SELECT * FROM leads WHERE origin='deepseek'").fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["intent_label"], "potential")
                self.assertEqual(rows[0]["ai_verdict"], "potential")
                self.assertEqual(rows[0]["ai_confidence"], 86)
            self.assertEqual(ds.status()["search_used"], 3)
            self.assertEqual(ds.status()["analysis_used"], 1)
            self.assertTrue(notify.called)

    def test_article_not_marked_as_client(self):
        item = {
            "title": "Банк объявил о новых тарифах РКО",
            "url": "https://example.org/news",
            "snippet": "Новости и обзор банковских тарифов",
        }
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-deepseek-key", "DEEPSEEK_ANALYZE": "1"}), \
             patch.object(ds, "_search", return_value=[item]), \
             patch.object(ds, "_analyze") as analyze, \
             patch.object(ds, "new_opportunities"):
            ds.run_once()
            self.assertFalse(analyze.called)
            with db.connect() as cx:
                self.assertEqual(cx.execute("SELECT COUNT(*) FROM leads").fetchone()[0], 0)

    def test_search_only_does_not_call_model(self):
        item = {"title": "Подскажите, какой банк выбрать для ИП?",
                "url": "https://example.org/search-only",
                "summary": "Хочу открыть расчётный счёт для ИП"}
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-deepseek-key", "DEEPSEEK_ANALYZE": "0"}), \
             patch.object(ds, "_search", return_value=[item]), \
             patch.object(ds, "_analyze") as analyze, \
             patch.object(ds, "new_opportunities"):
            ds.run_once()
            self.assertFalse(analyze.called)
            self.assertEqual(ds.status()["analysis_used"], 0)
            self.assertFalse(ds.status()["ai_enabled"])

    def test_budget_never_exceeds_max(self):
        for _ in range(ds.SEARCH_DAILY_LIMIT):
            self.assertTrue(ds._reserve("deepseek_search", ds.SEARCH_DAILY_LIMIT))
        self.assertFalse(ds._reserve("deepseek_search", ds.SEARCH_DAILY_LIMIT))
        self.assertEqual(ds._used_today("deepseek_search"), ds.SEARCH_DAILY_LIMIT)

    def test_review_preserves_stage_and_notes(self):
        with db.connect() as cx:
            cur = cx.execute(
                """INSERT INTO leads(title,description,url,category,score,created_at,stage,notes,origin)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                ("Подскажите какой банк для ИП?", "Ищу расчётный счёт", "https://example.org/lead",
                 "РКО", 75, db.now(), "review", "Написать после проверки", "rss"),
            )
            lead_id = cur.lastrowid
        ai = {"verdict": "information", "category": "РКО", "confidence": 91,
              "reason": "Статья, а не личный запрос", "reply": ""}
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-deepseek-key", "DEEPSEEK_ANALYZE": "1"}), \
             patch.object(ds, "_analyze", return_value=ai):
            ds.analyze_lead(lead_id)
        with db.connect() as cx:
            row = cx.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
            self.assertEqual(row["stage"], "review")
            self.assertEqual(row["notes"], "Написать после проверки")
            self.assertEqual(row["intent_label"], "information")
            self.assertLessEqual(row["score"], 35)


if __name__ == "__main__":
    unittest.main()
