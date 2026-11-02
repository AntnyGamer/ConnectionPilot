"""One-time dependency setup, then launch the desktop app."""
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import subprocess
import sys

if sys.version_info < (3, 11):
    input("Python 3.11+ required. Press Enter to exit.")
    raise SystemExit(1)

# Install the versions tested with this release rather than arbitrary future versions.
required = {"playwright": "1.57.0", "tzdata": "2026.2"}
try:
    needs_install = any(version(name) != wanted for name, wanted in required.items())
except PackageNotFoundError:
    needs_install = True
if needs_install:
    print("Installing pinned application dependencies. Internet required on first launch...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                           "-r", "requirements.txt"])

from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    chromium_present = Path(playwright.chromium.executable_path).is_file()
if not chromium_present:
    print("Installing the Playwright Chromium browser...")
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])

subprocess.check_call([sys.executable, "main.py"])
