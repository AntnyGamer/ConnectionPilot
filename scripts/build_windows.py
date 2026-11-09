"""Build the ConnectionPilot v1.1.0 Windows executable. Run on Windows, never cross-compile from Linux."""
from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    if sys.platform != "win32":
        raise RuntimeError("Windows builds must be performed on Windows")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onefile", "--windowed", "--name", "ConnectionPilot",
            "--icon", str(ROOT / "assets/connectionpilot.ico"),
            "--version-file", str(ROOT / "assets/connectionpilot_version.txt"),
            "--add-data", str(ROOT / "assets/connectionpilot.ico") + ";assets",
            "--add-data", str(ROOT / "assets/connectionpilot.png") + ";assets",
            "--add-data", str(ROOT / "assets/connectionpilot-header.png") + ";assets",
            "--add-data", str(ROOT / "THIRD-PARTY-NOTICES.txt") + ";.",
            "--collect-all", "playwright",
            # Keep the complete IANA rules, but only package tzdata's files:
            # PyInstaller compresses them inside its onefile archive.
            "--collect-data", "tzdata",
            # ZoneInfo imports region packages dynamically by string name.
            "--collect-submodules", "tzdata.zoneinfo",
            "--distpath", str(ROOT / "dist"),
            str(ROOT / "main.py")]
    subprocess.check_call(args, cwd=ROOT)
    output = ROOT / "dist/ConnectionPilot.exe"
    if not output.is_file() or output.stat().st_size < 100_000:
        raise RuntimeError("Windows executable wasn't produced")
    # Ship the same notices as an accessible file alongside the one-file EXE.
    shutil.copyfile(ROOT / "THIRD-PARTY-NOTICES.txt", output.parent / "THIRD-PARTY-NOTICES.txt")
    print(f"Built {output}: {output.stat().st_size:,} bytes")


if __name__ == "__main__":
    build()
