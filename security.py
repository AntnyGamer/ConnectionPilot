"""Fail-closed origin checking and minimal, local diagnostic text.

This module does not evade site restrictions or disguise browser automation.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit


TRUSTED_HOSTS = frozenset({"linkedin.com", "www.linkedin.com"})


def is_trusted_linkedin_page(url: str) -> bool:
    """Only permit HTTPS pages on the exact LinkedIn hosts."""
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in TRUSTED_HOSTS
                and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None)
    except (TypeError, ValueError):
        return False


def safe_diagnostic(error: BaseException, limit: int = 160) -> str:
    """Avoid copying raw multi-line browser diagnostics and URL tokens to logs."""
    text = str(error).splitlines()[0].strip()
    # Browser errors occasionally include links with OAuth state, codes or tokens.
    text = re.sub(r"https?://\S+", "[URL redacted]", text, flags=re.I)
    text = re.sub(r"(?i)\b(?:authorization|cookie|token|password|secret|session)\b\s*[:=]\s*\S+",
                  "[credential redacted]", text)
    return text[:limit]


def restrict_posix_data(path: Path) -> None:
    """On POSIX restrict new local data to its owner; Windows uses account ACLs."""
    if os.name != "posix":
        return
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
