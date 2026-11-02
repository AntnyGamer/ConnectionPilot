"""Fail CI when a file with a secret-prone name enters the source tree.

This is a lightweight guard, not a replacement for GitHub secret scanning.
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RISKY = re.compile(r"(?i)(?:^|/)(?:google-login-[^/]+|browser-profile|ConnectionPilot)(?:/|$)|"
                   r"\.sqlite3(?:-[^/]*)?$|\.db(?:-[^/]*)?$|(?:^|/)\.env(?:\.[^/]*)?$|"
                   r"(?:^|/)(?:cookies?|sessions?|token|secrets?)\.[^/]+$|"
                   r"\.(?:pem|p12|key)$")


def listed_files() -> list[str]:
    try:
        result = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'],
                                check=True, capture_output=True)
        return [s.decode('utf-8', 'surrogateescape') for s in result.stdout.split(b'\0') if s]
    except (FileNotFoundError, subprocess.CalledProcessError):
        return [str(p.relative_to(ROOT)).replace(os.sep, '/')
                for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.parts]


def main() -> int:
    risky = [name for name in listed_files() if RISKY.search(name)]
    if risky:
        print('Refusing publication: sensitive-looking paths detected:', *risky, sep='\n - ')
        return 1
    print('Pre-publication filename scan passed. No session directories or databases tracked.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
