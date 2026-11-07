"""Validate Eastern scheduling without a packaged timezone database."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from eastern_time import TZ, EasternTime
from logic import seconds_until_tomorrow, seconds_until_work_hours


class EasternTimeTests(unittest.TestCase):
    def test_winter_and_summer_offsets(self):
        for year in (2026, 2027, 2030, 2040, 2050):
            with self.subTest(year=year):
                self.assertEqual(datetime(year, 1, 15, tzinfo=TZ).utcoffset(),
                                 timedelta(hours=-5))
                self.assertEqual(datetime(year, 7, 15, tzinfo=TZ).utcoffset(),
                                 timedelta(hours=-4))

    def test_spring_forward_skips_missing_local_hour(self):
        before = datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc).astimezone(TZ)
        after = datetime(2026, 3, 8, 7, tzinfo=timezone.utc).astimezone(TZ)
        self.assertEqual((before.hour, before.minute, before.tzname()), (1, 59, "EST"))
        self.assertEqual((after.hour, after.minute, after.tzname()), (3, 0, "EDT"))
        self.assertEqual(seconds_until_work_hours(before, 8, 20), 18060)
        self.assertEqual(seconds_until_tomorrow(before), 75660)

    def test_fall_back_repeats_hour_with_distinct_folds(self):
        first = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc).astimezone(TZ)
        second = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc).astimezone(TZ)
        self.assertEqual((first.hour, first.minute, first.fold, first.tzname()),
                         (1, 30, 0, "EDT"))
        self.assertEqual((second.hour, second.minute, second.fold, second.tzname()),
                         (1, 30, 1, "EST"))
        self.assertEqual((second.astimezone(timezone.utc) -
                          first.astimezone(timezone.utc)).total_seconds(), 3600)
        self.assertEqual(seconds_until_work_hours(second, 8, 20), 23400)

    def test_no_timezone_database_is_packaged(self):
        root = Path(__file__).resolve().parents[1]
        builder = (root / "scripts/build_windows.py").read_text()
        requirements = (root / "requirements.txt").read_text()
        self.assertIn('"--exclude-module", "tzdata"', builder)
        self.assertNotIn('zoneinfo/America', builder)
        self.assertNotIn('zone_file', builder)
        self.assertNotIn('tzdata==', requirements)
        self.assertEqual(TZ.key, "America/New_York")
        self.assertIsInstance(TZ, EasternTime)


if __name__ == "__main__":
    unittest.main()
