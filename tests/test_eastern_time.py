"""Check full IANA Eastern timezone rules and packaged data declarations."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from zoneinfo import ZoneInfo
from eastern_time import TZ
from logic import seconds_until_tomorrow, seconds_until_work_hours


class EasternTimeTests(unittest.TestCase):
    def test_real_iana_offsets(self):
        self.assertEqual(TZ.key, "America/New_York")
        self.assertIsInstance(TZ, ZoneInfo)
        for year in (1990, 2006, 2026, 2027, 2030, 2040, 2050):
            with self.subTest(year=year):
                self.assertEqual(datetime(year, 1, 15, tzinfo=TZ).utcoffset(),
                                 timedelta(hours=-5))
                self.assertEqual(datetime(year, 7, 15, tzinfo=TZ).utcoffset(),
                                 timedelta(hours=-4))

    def test_spring_forward(self):
        before = datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc).astimezone(TZ)
        after = datetime(2026, 3, 8, 7, tzinfo=timezone.utc).astimezone(TZ)
        self.assertEqual((before.hour, before.minute, before.tzname()), (1, 59, "EST"))
        self.assertEqual((after.hour, after.minute, after.tzname()), (3, 0, "EDT"))
        self.assertEqual(seconds_until_work_hours(before, 8, 20), 18060)
        self.assertEqual(seconds_until_tomorrow(before), 75660)

    def test_fall_back(self):
        first = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc).astimezone(TZ)
        second = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc).astimezone(TZ)
        self.assertEqual((first.hour, first.fold, second.hour, second.fold), (1, 0, 1, 1))
        self.assertEqual((first.tzname(), second.tzname()), ("EDT", "EST"))
        self.assertEqual(seconds_until_work_hours(second, 8, 20), 23400)

    def test_historical_dst_change(self):
        # 2006 used April/October transitions rather than March/November.
        self.assertEqual(datetime(2006, 3, 20, tzinfo=TZ).utcoffset(),
                         timedelta(hours=-5))
        self.assertEqual(datetime(2007, 3, 20, tzinfo=TZ).utcoffset(),
                         timedelta(hours=-4))

    def test_complete_tzdata_packed_not_all_package_metadata(self):
        root = Path(__file__).resolve().parents[1]
        builder = (root / "scripts/build_windows.py").read_text()
        requirements = (root / "requirements.txt").read_text()
        self.assertIn('"--collect-data", "tzdata"', builder)
        self.assertNotIn('"--collect-all", "tzdata"', builder)
        self.assertIn("tzdata==2026.6", requirements)


if __name__ == "__main__":
    unittest.main()
