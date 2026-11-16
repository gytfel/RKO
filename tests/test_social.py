"""Offline TGStat and VK adapter tests; never call live provider APIs."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class SocialMonitoringTests(unittest.TestCase):
    def setUp(self):
        import db
        import social_discovery as social
        self.db,self.social=db,social
        self.tmp=tempfile.TemporaryDirectory()
        self.original=(db.DATA_DIR,db.DB_FILE)
        db.DATA_DIR=self.tmp.name
        db.DB_FILE=os.path.join(self.tmp.name,"radar.sqlite3")
        db.initialize()
        social.STATUS.update(running=False,added=0,checked=0,received=0,filtered=0,duplicates=0,errors=[])

    def tearDown(self):
        self.db.DATA_DIR,self.db.DB_FILE=self.original
        self.tmp.cleanup()

    def test_disabled_without_key(self):
        with patch.dict(os.environ,{"TGSTAT_API_TOKEN":"","VK_ACCESS_TOKEN":"","VK_WALL_DOMAINS":""}):
            self.assertFalse(self.social.configured())
            self.assertFalse(self.social.start_refresh())

    def test_telegram_post_date_and_public_link(self):
        ts=int(datetime.now(timezone.utc).timestamp())
        post={"date":ts,"link":"t.me/public_bank_chat/7239",
              "text":"Подскажите, какую дебетовую карту выбрать? Хочу оформить."}
        entry=self.social._tg_candidate(post)
        self.assertEqual(entry["origin"],"tgstat")
        self.assertEqual(entry["url"],"https://t.me/public_bank_chat/7239")
        self.assertIsNone(self.social._tg_candidate(dict(post,date=int((datetime.now(timezone.utc)-timedelta(days=1000)).timestamp()))))
        self.assertIsNone(self.social._tg_candidate(dict(post,link="t.me/c/123456/456")))
        self.assertIsNone(self.social._tg_candidate(dict(post,forwarded_from=11)))

    def test_vk_post_public_wall_and_timestamp(self):
        now=int(datetime.now(timezone.utc).timestamp())
        post={"owner_id":-12345,"id":901,"date":now,
              "text":"Кто подскажет банк для ИП? Нужен расчетный счет"}
        entry=self.social._vk_candidate(post)
        self.assertEqual(entry["url"],"https://vk.com/wall-12345_901")
        self.assertIsNone(self.social._vk_candidate(dict(post,date=1000000000)))

    def test_api_search_saves_one_record_and_dedupes(self):
        ts=int(datetime.now(timezone.utc).timestamp())
        item={"date":ts,"link":"t.me/public_bank_chat/456",
              "text":"Ищу реферальную ссылку на дебетовую карту Альфа-Банка, хочу оформить"}
        with patch.dict(os.environ,{"TGSTAT_API_TOKEN":"fake-local-token","VK_ACCESS_TOKEN":"","VK_WALL_DOMAINS":""}), \
             patch.object(self.social,"_tg_search",return_value=[item]) as search, \
             patch.object(self.social,"new_opportunities"):
            self.assertTrue(self.social.run_once())
            self.assertEqual(search.call_count,1)
            self.assertTrue(self.social.run_once())
        with self.db.connect() as cx:
            rows=cx.execute("SELECT * FROM leads WHERE origin='tgstat'").fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["category"],"Карты")
            self.assertEqual(rows[0]["referral_signal"],"asked")
        self.assertGreaterEqual(self.social.status()["duplicates"],1)

    def test_vk_only_named_communities(self):
        now=int(datetime.now(timezone.utc).timestamp())
        post={"owner_id":-120,"id":49,"date":now,
              "text":"Какой банк выбрать для нового ИП? Хочу открыть расчетный счет"}
        with patch.dict(os.environ,{"TGSTAT_API_TOKEN":"","VK_ACCESS_TOKEN":"fake-local-token",
                                     "VK_WALL_DOMAINS":"firstcommunity,secondcommunity"}), \
             patch.object(self.social,"_vk_search",return_value=[post]) as search, \
             patch.object(self.social,"new_opportunities"):
            self.social.run_once()
            self.assertEqual(search.call_count,1)
            self.assertEqual(search.call_args_list[0].args[0],"firstcommunity")
        with self.db.connect() as cx:
            self.assertEqual(cx.execute("SELECT COUNT(*) FROM leads WHERE origin='vk'").fetchone()[0],1)

    def test_budget_limit(self):
        for i in range(self.social.TG_DAILY_LIMIT):
            self.assertTrue(self.social._reserve("social_tg",self.social.TG_DAILY_LIMIT))
        self.assertFalse(self.social._reserve("social_tg",self.social.TG_DAILY_LIMIT))


if __name__=="__main__":
    unittest.main()
