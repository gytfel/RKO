"""Strict freshness filters: old and undated web posts never become new leads."""
from datetime import date, timedelta
import unittest

from freshness import date_status, fresh_publication, normalize_published_date, old_year_in_url, window_bounds


class FreshnessTests(unittest.TestCase):
    def test_utc_iso_and_rfc2822(self):
        self.assertEqual(normalize_published_date("2026-11-15T23:30:00-02:00", today=date(2026,11,16)), "2026-11-16")
        self.assertEqual(normalize_published_date("Mon, 16 Nov 2026 11:00:00 GMT", today=date(2026,11,16)), "2026-11-16")
        self.assertEqual(normalize_published_date("2022-11-15", today=date(2026,11,16)), "2022-11-15")

    def test_cannot_use_collection_date(self):
        now = date(2026,11,16)
        self.assertEqual(date_status(None, today=now), "unknown")
        self.assertEqual(date_status("yesterday", today=now), "unknown")
        self.assertFalse(fresh_publication("", today=now))

    def test_four_year_old_is_rejected(self):
        today = date(2026,11,16)
        self.assertEqual(date_status("2022-11-16", today=today), "stale")
        self.assertFalse(fresh_publication("2022-11-16", "https://example.com/forum/post", today=today))

    def test_boundary_and_future(self):
        today = date(2026,11,16)
        self.assertEqual(window_bounds(today=today), ("2026-11-09", "2026-11-16"))
        self.assertTrue(fresh_publication("2026-11-12", today=today))
        self.assertFalse(fresh_publication("2026-11-08", today=today))
        self.assertFalse(fresh_publication("2026-11-17", today=today))

    def test_old_article_archive(self):
        today = date(2026,11,16)
        self.assertTrue(old_year_in_url("https://example.com/2022/11/old-article", today=today))
        self.assertFalse(fresh_publication("2026-11-16", "https://example.com/2022/11/old-article", today=today))
        self.assertFalse(old_year_in_url("https://example.com/threads/post-2022-about-banks", today=today))


if __name__ == "__main__":
    unittest.main()
