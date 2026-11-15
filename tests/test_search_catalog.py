"""Search quotas and balanced query coverage. No network calls."""
import unittest

from search_catalog import (
    CARD_QUERIES, REFERRAL_QUERIES, RKO_QUERIES,
    deepseek_plan, tavily_plan,
)


class DiscoveryCoverageTests(unittest.TestCase):
    def test_tavily_balanced(self):
        for cursor in (0, 1, 5, 17, 100):
            plan = tavily_plan(cursor)
            self.assertEqual(len(plan), 5)
            self.assertIn(plan[0], RKO_QUERIES)
            self.assertIn(plan[1], CARD_QUERIES)
            self.assertIn(plan[2], REFERRAL_QUERIES)
            self.assertIn(plan[3], CARD_QUERIES)
            self.assertIn(plan[4], RKO_QUERIES)

    def test_deepseek_balanced(self):
        for cursor in (0, 1, 50):
            plan = deepseek_plan(cursor)
            self.assertEqual(len(plan), 3)
            self.assertIn(plan[0], RKO_QUERIES)
            self.assertIn(plan[1], CARD_QUERIES)
            self.assertIn(plan[2], REFERRAL_QUERIES)

    def test_rotation_changes_searches(self):
        self.assertNotEqual(tavily_plan(0), tavily_plan(1))
        self.assertNotEqual(deepseek_plan(0), deepseek_plan(1))

    def test_public_sites_and_card_relevance(self):
        combined = " ".join(RKO_QUERIES + CARD_QUERIES + REFERRAL_QUERIES)
        for host in ("hranidengi.com", "banki.ru", "vc.ru", "pikabu.ru"):
            self.assertIn(host, combined)
        self.assertIn("дебетовую карту", combined)
        self.assertIn("реферальную ссылку", combined)


if __name__ == "__main__":
    unittest.main()
