import unittest
from scoring import score_text

class ScoringTests(unittest.TestCase):
    def test_rko_intent_high(self):
        x = score_text("Подскажите, какой банк выбрать для ИП?", "Хочу открыть расчётный счёт для нового ИП, посоветуйте РКО")
        self.assertEqual(x["category"], "РКО")
        self.assertGreaterEqual(x["score"], 65)

    def test_registration(self):
        x = score_text("Как открыть ИП?", "Собираюсь зарегистрировать ИП, нужна помощь с оформлением")
        self.assertEqual(x["category"], "Регистрация бизнеса")
        self.assertGreaterEqual(x["score"], 60)

    def test_marketplace_only_not_auto_lead(self):
        x = score_text("Новости Ozon", "Обзор тарифов маркетплейса")
        self.assertEqual(x["category"], "Маркетплейсы")
        self.assertLess(x["score"], 65)

    def test_unrelated(self):
        x = score_text("Рецепт блинчиков", "Как приготовить быстро")
        self.assertEqual(x["category"], "Другое")
        self.assertEqual(x["score"], 0)

if __name__ == '__main__': unittest.main()
