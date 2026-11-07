"""Eastern-time rules shared by the scheduling and storage code.

Frozen Windows builds contain one IANA timezone file (America/New_York),
not the full tzdata package. Source installations can use system/tzdata rules.
"""
from __future__ import annotations

import sys
from pathlib import Path
from zoneinfo import ZoneInfo


def eastern_zone():
    if getattr(sys, "frozen", False):
        root = Path(sys._MEIPASS)
        file = root / "zoneinfo" / "America" / "New_York"
        with file.open("rb") as stream:
            return ZoneInfo.from_file(stream, key="America/New_York")
    return ZoneInfo("America/New_York")


TZ = eastern_zone()
