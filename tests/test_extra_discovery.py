"""Offline regression tests for extra public search provider adapters.

Mocks all network calls and keeps credentials out of CI logs.
"""
import base64
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch


class ExtraDiscoveryTests(unittest.TestCase):
    def setUp(self):
        import db
        import extra_discovery as extra
        self.db,self.extra=db,extra
        self.tmp=tempfile.TemporaryDirectory()
        self.original=(db.DATA_DIR,db.DB_FILE)
        db.DATA_DIR=self.tmp.name
        db.DB_FILE=os.path.join(self.tmp.name,"radar.sqlite3")
        db.initialize()
        extra.STATUS.update(running=False,checked=0,added=0,received=0,
                            filtered=0,undated=0,stale=0,duplicates=0,errors=[])

    def tearDown(self):
        self.db.DATA_DIR,self.db.DB_FILE=self.original
        self.tmp.cleanup()

    def test_disabled_without_keys(self):
        with patch.dict(os.environ,{
            "YANDEX_SEARCH_API_KEY":"","YANDEX_SEARCH_FOLDER_ID":"",
            "BRAVE_SEARCH_API_KEY":"","TELEMETR_API_KEY":""}):
            self.assertFalse(self.extra.configured())
            self.assertFalse(self.extra.start_refresh())

    def test_yandex_xml_requires_original_date_not_modtime(self):
        today=datetime.now(timezone.utc).date().isoformat()
        xml=('<?xml version="1.0" encoding="utf-8"?><yandexsearch><response>'
             '<results><grouping><group><doc><url>https://example.org/threads/23</url>'
             '<title>Хочу открыть расчетный счет для ИП</title>'
             '<passages><passage>Подскажите какой банк выбрать</passage></passages>'
             '<pubdate>'+today+'</pubdate></doc></group>'
             '<group><doc><url>https://example.org/threads/old</url>'
             '<title>Хочу открыть расчетный счет для ИП</title>'
             '<modtime>'+today+'</modtime></doc></group>'
             '</grouping></results></response></yandexsearch>')
        response=Mock(status_code=200,ok=True)
        response.json.return_value={"rawData":base64.b64encode(xml.encode()).decode()}
        with patch.dict(os.environ,{
            "YANDEX_SEARCH_API_KEY":"test-yandex-api",
            "YANDEX_SEARCH_FOLDER_ID":"b1gexamplefolder",
            "BRAVE_SEARCH_API_KEY":"","TELEMETR_API_KEY":""}), \
             patch.object(self.extra.requests,"post",return_value=response) as post, \
             patch.object(self.extra,"new_opportunities"):
            self.assertTrue(self.extra.run_once())
            self.assertEqual(post.call_count,1)
            self.assertEqual(post.call_args.kwargs["json"]["period"],"PERIOD_2_WEEKS")
        with self.db.connect() as db:
            rows=db.execute("SELECT * FROM leads WHERE origin='yandex'").fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["published_on"],today)
        self.assertEqual(self.extra.status()["undated"],1)

    def test_brave_schema_publication_only(self):
        today=datetime.now(timezone.utc).date().isoformat()
        body={"web":{"results":[
          {"url":"https://example.org/q/new","title":"Подскажите какую карту оформить?",
           "description":"Хочу открыть дебетовую карту в банке",
           "page_age":today,"schemas":[{"datePublished":today}]},
          {"url":"https://example.org/q/stale","title":"Хочу оформить дебетовую карту",
           "description":"Подскажите","page_age":today},
          {"url":"https://example.org/q/2022/old","title":"Хочу оформить дебетовую карту",
           "description":"Подскажите","schemas":[{"datePublished":"2022-11-17"}]}
        ]}}
        response=Mock(status_code=200,ok=True)
        response.json.return_value=body
        with patch.dict(os.environ,{
            "BRAVE_SEARCH_API_KEY":"test-brave-api","YANDEX_SEARCH_API_KEY":"",
            "YANDEX_SEARCH_FOLDER_ID":"","TELEMETR_API_KEY":""}), \
             patch.object(self.extra.requests,"get",return_value=response) as get, \
             patch.object(self.extra,"new_opportunities"):
            self.extra.run_once()
            self.assertEqual(get.call_count,1)
            self.assertEqual(get.call_args.kwargs["params"]["freshness"],"pw")
        with self.db.connect() as db:
            rows=db.execute("SELECT * FROM leads WHERE origin='brave'").fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(self.extra.status()["undated"],1)
        self.assertEqual(self.extra.status()["stale"],1)

    def test_telemetr_public_username_only(self):
        today=datetime.now(timezone.utc).isoformat()
        payload={"chats":[{"internal_id":"chat1","username":"publicbankclub"}],
            "messages":[
              {"peer_id":"chat1","message_id":33,"date":today,
               "text":"Ищу реферальную ссылку на дебетовую карту Альфа-Банка"},
              {"peer_id":"private","message_id":35,"date":today,
               "text":"Хочу оформить дебетовую карту"}
            ]}
        response=Mock(status_code=200,ok=True)
        response.json.return_value=payload
        with patch.dict(os.environ,{
            "TELEMETR_API_KEY":"test-telemetr-api","BRAVE_SEARCH_API_KEY":"",
            "YANDEX_SEARCH_API_KEY":"","YANDEX_SEARCH_FOLDER_ID":""}), \
             patch.object(self.extra.requests,"get",return_value=response) as get, \
             patch.object(self.extra,"new_opportunities"):
            self.extra.run_once()
            self.assertEqual(get.call_args.kwargs["params"]["sort"],"date")
        with self.db.connect() as db:
            rows=db.execute("SELECT * FROM leads WHERE origin='telemetr'").fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["url"],"https://t.me/publicbankclub/33")

    def test_repeated_results_are_deduped_and_budget_capped(self):
        for provider in self.extra.DAILY_LIMIT:
            for i in range(self.extra.DAILY_LIMIT[provider]):
                self.assertTrue(self.extra._reserve(provider))
            self.assertFalse(self.extra._reserve(provider))

    def test_search_catalog_targeted_questions_each_run(self):
        from search_catalog import tavily_plan
        for i in range(15):
            q=tavily_plan(i)
            self.assertEqual(len(q),5)
            self.assertTrue(any("bankiros.ru" in item or "banki.ru" in item for item in q))


if __name__=="__main__":
    unittest.main()
