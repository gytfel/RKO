"""Offline regression tests for v4 scoring and migration. No external API needed."""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scoring import score_text


class Scoring(unittest.TestCase):
    def test_real_question(self):
        d = score_text('Где открыть расчётный счёт для ИП?')
        self.assertEqual(d['intent_label'], 'potential')
        self.assertEqual(d['category'], 'РКО')

    def test_blog_not_buyer(self):
        for title in ('Как открыть ИП: пошаговая инструкция',
                      'Альфа-Банк заявил об изменении тарифов РКО'):
            self.assertNotEqual(score_text(title)['intent_label'], 'potential')

    def test_informal_question(self):
        self.assertEqual(score_text('Подскажите, какой банк лучше для ИП?')['intent_label'], 'potential')

    def test_card_question_is_relevant(self):
        d = score_text('Подскажите, какую дебетовую карту выбрать?', 'Хочу оформить карту для покупок')
        self.assertEqual(d['category'], 'Карты')
        self.assertEqual(d['intent_label'], 'potential')

    def test_rko_statement_without_question_mark(self):
        d = score_text('Ищу банк для ИП', 'Мне нужен расчётный счёт')
        self.assertEqual(d['category'], 'РКО')
        self.assertEqual(d['intent_label'], 'potential')

    def test_card_news_not_customer(self):
        d = score_text('Банк объявил о новых дебетовых картах', 'Новости и обзор тарифов')
        self.assertEqual(d['category'], 'Карты')
        self.assertNotEqual(d['intent_label'], 'potential')

    def test_marketplace_news(self):
        self.assertNotEqual(score_text('Новости Wildberries: обновление тарифов')['intent_label'], 'potential')


class DatabaseMigration(unittest.TestCase):
    def test_v3_records_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = os.environ.get('DATA_DIR')
            try:
                os.environ['DATA_DIR'] = tmp
                # db.DATA_DIR bound at import, so load a separate copy under a unique name.
                import importlib.util
                spec = importlib.util.spec_from_file_location('rko_test_db', Path(__file__).resolve().parents[1]/'db.py')
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                cx=sqlite3.connect(mod.DB_FILE)
                cx.execute("""CREATE TABLE leads(id INTEGER PRIMARY KEY,source_id INTEGER,title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',url TEXT NOT NULL UNIQUE,category TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '',stage TEXT NOT NULL DEFAULT 'new',
                    notes TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,published_at TEXT)""")
                cx.execute("INSERT INTO leads(title,url,category,stage,notes,created_at) VALUES (?,?,?,?,?,?)",
                    ('Где открыть расчётный счёт для ИП?', 'https://example.org/question', 'Другое', 'review', 'Не удалить', '2026-11-15'))
                cx.commit();cx.close()
                mod.initialize();self.assertEqual(mod.rescore_existing(),1)
                with mod.connect() as db:
                    r=db.execute('SELECT * FROM leads').fetchone()
                    self.assertEqual(r['stage'],'review')
                    self.assertEqual(r['notes'],'Не удалить')
                    self.assertEqual(r['intent_label'],'potential')
                self.assertEqual(mod.rescore_existing(),0)
            finally:
                if prev is None: os.environ.pop('DATA_DIR',None)
                else: os.environ['DATA_DIR'] = prev

if __name__ == '__main__':
    unittest.main()
