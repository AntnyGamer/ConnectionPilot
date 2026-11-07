"""Build the ConnectionPilot v1.0.6 Windows executable. Run on Windows, never cross-compile from Linux."""
from __future__ import annotations
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    if sys.platform != "win32":
        raise RuntimeError("Windows builds must be performed on Windows")
    # Only New York's timezone rules are needed. This file is about 2 KB,
    # whereas bundling the entire tzdata distribution is unnecessary.
    zone_file = Path(str(files("tzdata.zoneinfo.America").joinpath("New_York")))
    if not zone_file.is_file() or zone_file.stat().st_size > 50_000:
        raise RuntimeError("Required compact Eastern timezone data is missing or too large")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onefile", "--windowed", "--name", "ConnectionPilot",
            "--icon", str(ROOT / "assets/connectionpilot.ico"),
            "--version-file", str(ROOT / "assets/connectionpilot_version.txt"),
            "--add-data", str(ROOT / "assets/connectionpilot.ico") + ";assets",
            "--add-data", str(ROOT / "assets/connectionpilot.png") + ";assets",
            "--add-data", str(ROOT / "assets/connectionpilot-header.png") + ";assets",
            "--collect-all", "playwright",
            "--add-data", str(zone_file) + ";zoneinfo/America",
            "--exclude-module", "tzdata",
            "--distpath", str(ROOT / "dist"),
            str(ROOT / "main.py")]
    subprocess.check_call(args, cwd=ROOT)
    output = ROOT / "dist/ConnectionPilot.exe"
    if not output.is_file() or output.stat().st_size < 100_000:
        raise RuntimeError("Windows executable wasn't produced")
    print(f"Built {output}: {output.stat().st_size:,} bytes")


if __name__ == "__main__":
    build()
