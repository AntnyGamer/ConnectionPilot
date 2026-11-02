"""One-time dependency setup, then launch the desktop app."""
from importlib.metadata import PackageNotFoundError, version
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def pinned_dependencies():
    """Read the project's exact package pins from its requirements file."""
    requirements = {}
    for line in (BASE_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, separator, wanted = line.partition("==")
        if not separator or not name.strip() or not wanted.strip():
            raise ValueError(f"Expected an exact dependency pin: {line!r}")
        requirements[name.strip()] = wanted.strip()
    if not requirements:
        raise ValueError("No pinned dependencies found in requirements.txt")
    return requirements


def main():
    if sys.version_info < (3, 11):
        input("Python 3.11+ required. Press Enter to exit.")
        raise SystemExit(1)

    # Use the same exact pins that pip and Dependabot read. Do not duplicate
    # version numbers here, or dependency updates can become ineffective.
    required = pinned_dependencies()
    try:
        needs_install = any(version(name) != wanted for name, wanted in required.items())
    except PackageNotFoundError:
        needs_install = True
    if needs_install:
        print("Installing pinned application dependencies. Internet required on first launch...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                               "-r", str(BASE_DIR / "requirements.txt")])

    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        chromium_present = (Path(playwright.chromium.executable_path).is_file())
    if not chromium_present:
        print("Installing the Playwright Chromium browser...")
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])

    # Never depend on the shell's current directory for program files.
    subprocess.check_call([sys.executable, str(BASE_DIR / "main.py")], cwd=BASE_DIR)


if __name__ == "__main__":
    main()
