"""Sign in using a normal system browser before starting browser automation.

Google may reject scripted browsers. This module deliberately launches an
ordinary Chrome/Edge process with a separate, local browser profile; it never
collects credentials, cookies or OAuth tokens.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

LOGIN_URL = "https://www.linkedin.com/login"
SUPPORTED = ("chrome", "msedge")


def browser_profile(data_dir: Path, channel: str) -> Path:
    if channel not in SUPPORTED:
        raise ValueError("Unsupported browser channel")
    return Path(data_dir) / ("google-login-" + channel)


def find_browser():
    """Return (Playwright channel, executable) for installed Chrome or Edge."""
    env = os.environ
    candidates = (
        ("chrome", (
            Path(env.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(env.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
            Path(env.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Google/Chrome/Application/chrome.exe",
            shutil.which("chrome"), shutil.which("chrome.exe"),
        )),
        ("msedge", (
            Path(env.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
            Path(env.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
            Path(env.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Microsoft/Edge/Application/msedge.exe",
            shutil.which("msedge"), shutil.which("msedge.exe"),
        )),
    )
    for channel, paths in candidates:
        for path in paths:
            if path and Path(path).is_file():
                return channel, str(Path(path))
    raise FileNotFoundError("Install Google Chrome or Microsoft Edge to use Google sign-in.")


def open_google_signin(data_dir: Path):
    """Open LinkedIn login in a real browser with a separate persistent profile.

    Returns the channel to be used *after the human closes the login browser*.
    """
    channel, executable = find_browser()
    profile = browser_profile(data_dir, channel)
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        executable, f"--user-data-dir={profile}", "--no-first-run",
        "--new-window", LOGIN_URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return channel
