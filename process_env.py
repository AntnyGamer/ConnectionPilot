"""Keep system browsers from inheriting PyInstaller's private DLL search path."""
from __future__ import annotations

import sys


def prepare_external_processes():
    # PyInstaller configures Windows DLL loading for the extracted application.
    # External Chrome/Edge (and the Playwright driver) need the system DLL search
    # path instead, or they can fail to start inside a frozen application.
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes
        if not ctypes.windll.kernel32.SetDllDirectoryW(None):
            raise OSError("Could not reset Windows DLL search path for browser startup")
