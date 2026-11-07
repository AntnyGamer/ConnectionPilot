"""Eastern time backed by the complete, pinned IANA tzdata database.

The normal system zoneinfo search is used when available. Frozen Windows builds
bundle tzdata as compressed PyInstaller archive data for portable DST rules.
"""
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
