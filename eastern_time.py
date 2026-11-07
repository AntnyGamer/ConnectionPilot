"""US Eastern time for campaign scheduling without any timezone-data files.

The application only needs the US Eastern clock. These rules implement the
federal daylight-saving schedule in effect since 2007. Unlike an IANA database,
they cannot automatically reflect future changes to US daylight-saving law.
"""
from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

HOUR = timedelta(hours=1)
STANDARD = timedelta(hours=-5)


def _first_sunday(year: int, month: int) -> datetime:
    day = datetime(year, month, 1, 2)
    return day + timedelta(days=(6 - day.weekday()) % 7)


def _dst_bounds(year: int) -> tuple[datetime, datetime]:
    # Second Sunday in March, first Sunday in November, both at 2 AM.
    return _first_sunday(year, 3) + timedelta(days=7), _first_sunday(year, 11)


class EasternTime(tzinfo):
    """Eastern Standard/Daylight Time with correct repeated-hour folds."""
    key = "America/New_York"

    def tzname(self, dt):
        return "EDT" if self.dst(dt) else "EST"

    def utcoffset(self, dt):
        return STANDARD + self.dst(dt)

    def dst(self, dt):
        if dt is None:
            return timedelta(0)
        start, end = _dst_bounds(dt.year)
        local = dt.replace(tzinfo=None)
        if start + HOUR <= local < end - HOUR:
            return HOUR
        if end - HOUR <= local < end:
            return timedelta(0) if dt.fold else HOUR
        if start <= local < start + HOUR:
            return HOUR if dt.fold else timedelta(0)
        return timedelta(0)

    def fromutc(self, dt):
        if dt.tzinfo is not self:
            raise ValueError("fromutc requires this timezone")
        start, end = _dst_bounds(dt.year)
        standard = dt.replace(tzinfo=None) + STANDARD
        daylight = standard + HOUR
        if end <= daylight < end + HOUR:
            return standard.replace(tzinfo=self, fold=1)
        if standard < start or daylight >= end:
            return standard.replace(tzinfo=self)
        return daylight.replace(tzinfo=self)


TZ = EasternTime()
