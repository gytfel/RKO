"""Bounded and opt-in public-page extraction for AI analysis."""
import os
import tempfile
import unittest
from unittest.mock import patch,Mock


class ContextTests(unittest.TestCase):
    def setUp(self):
        import db
        import deep_context as context
        self.db,self.context=db,context
        self.tmp=tempfile.TemporaryDirectory()
        self.orig_dir,self.orig_file=db.DATA_DIR,db.DB_FILE
        db.DATA_DIR=self.tmp.name
        db.DB_FILE=os.path.join(self.tmp.name,"radar.sqlite3")
        db.initialize()

    def tearDown(self):
        self.db.DATA_DIR,self.db.DB_FILE=self.orig_dir,self.orig_file
        self.tmp.cleanup()

    def test_off_by_default(self):
        with patch.dict(os.environ,{"DEEP_CONTEXT":"0","TAVILY_API_KEY":"test-tavily",
                                    "DEEPSEEK_API_KEY":"test-ai","DEEPSEEK_ANALYZE":"1"}):
            self.assertFalse(self.context.enabled())
            with patch.object(self.context.requests,"post") as post:
                self.assertEqual(self.context.extract("https://hranidengi.com/threads/1/"),"")
                post.assert_not_called()

    def test_allowlist(self):
        self.assertTrue(self.context.safe_context_url("https://hranidengi.com/threads/bank.12/"))
        self.assertIsNone(self.context.safe_context_url("https://127.0.0.1/private"))
        self.assertIsNone(self.context.safe_context_url("https://t.me/closed_topic/5"))
        self.assertIsNone(self.context.safe_context_url("https://not-hanidengi.example/threads/2"))

    def test_extraction_redacts_and_limits(self):
        response=Mock(status_code=200,ok=True)
        response.json.return_value={"results":[{"raw_content":"Автор спрашивает: хочу карту. Пишите на foo@example.org, +7 999 111 2233. "+"А"*5000}]}
        with patch.dict(os.environ,{"DEEP_CONTEXT":"1","TAVILY_API_KEY":"test-tavily",
                                    "DEEPSEEK_API_KEY":"test-ai","DEEPSEEK_ANALYZE":"1"}), \
             patch.object(self.context.requests,"post",return_value=response) as post:
            text=self.context.extract("https://hranidengi.com/threads/bank.12/")
            self.assertTrue(text.startswith("Автор спрашивает"))
            self.assertNotIn("foo@example.org",text)
            self.assertNotIn("+7 999 111 2233",text)
            self.assertLessEqual(len(text),2800)
            self.assertEqual(post.call_count,1)
            self.assertEqual(self.context.usage(),1)

    def test_daily_limit(self):
        for _ in range(self.context.DAILY_LIMIT):
            self.assertTrue(self.context._reserve())
        self.assertFalse(self.context._reserve())


if __name__=="__main__":
    unittest.main()
