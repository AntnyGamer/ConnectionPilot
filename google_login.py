"""Sign in using a normal system browser before starting browser automation.

Google may reject scripted browsers. This module deliberately launches an
ordinary Chrome/Edge process with a separate, local browser profile; it never
collects credentials, cookies or OAuth tokens.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
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

    Returns (channel, owned_process) for later sign-in cleanup.
    """
    channel, executable = find_browser()
    profile = browser_profile(data_dir, channel)
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([
        executable, f"--user-data-dir={profile}", "--no-first-run",
        "--new-window", LOGIN_URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return channel, process


def close_google_signin(process):
    """Close only the dedicated sign-in browser launched by this app instance.

    The Chrome/Edge profile is separate from the user's regular browser.
    On Windows, taskkill /T targets the launcher's child process tree,
    which a plain Popen.terminate() could leave behind.
    """
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        cmd = ["taskkill", "/PID", str(process.pid), "/T"]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=4, check=False, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            # Use forced tree cleanup only if the graceful request failed.
            try:
                subprocess.run(cmd + ["/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=4, check=False,
                               creationflags=flags)
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
