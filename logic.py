"""Small, testable matching and scheduling helpers. No LinkedIn session is used here."""
from __future__ import annotations

import re
import math
import unicodedata
from datetime import datetime, timedelta, time, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
SEND_START_HOUR = 8
SEND_END_HOUR = 20

# Used by match_score when no terms are provided explicitly; not a default
# value for the application's search-term text field.
DEFAULT_MATCH_TERMS = [
    "politic", "government", "legislativ", "public policy", "public affairs",
    "diplomac", "international relations", "campaign", "congress", "civic",
    "rowan", "senat", "assembly", "public service", "council",
    "state department", "advocacy", "policy analyst", "staffer",
]

STOP_PHRASES = [
    "you've reached the invitation limit", "you’ve reached the invitation limit",
    "you've reached the weekly invitation limit", "you’ve reached the weekly invitation limit",
    "you're out of invitations", "you’re out of invitations",
    "invitation limit reached", "temporarily restricted", "your account has been restricted",
    "account restricted", "unusual activity", "security verification",
    "verify it's you", "verify it’s you", "captcha", "security challenge",
    "too many invitations",
]


def normalize_profile(url: str) -> str | None:
    """Allow genuine LinkedIn profile paths only (not posts, searches or tracking redirects)."""
    try:
        u = urlsplit(urljoin("https://www.linkedin.com", url.strip()))
        if u.scheme not in ("http", "https") or u.username or u.password or u.port not in (None, 80, 443) or u.hostname not in {"linkedin.com", "www.linkedin.com"}:
            return None
        parts = u.path.strip("/").split("/")
        if len(parts) != 2 or parts[0].lower() != "in":
            return None
        slug = parts[1]
        if not re.fullmatch(r"[A-Za-z0-9_%\-]+", slug) or len(slug) < 2:
            return None
        return urlunsplit(("https", "www.linkedin.com", f"/in/{slug}/", "", ""))
    except (TypeError, ValueError):
        return None


def search_result_name(text: str, label: str = "") -> str | None:
    """Read a visible member name or an explicit accessible profile-link label."""
    generic = {"connect", "follow", "message"}
    generic_profile = r"(?:view|see|open)\s+(?:(?:full|public|member|linkedin|their|my|your|this)\s+)?profile"
    lines = (text or "").splitlines()
    visible = " ".join(lines[0].split()) if lines else ""
    pattern = r"(?:view|see|open)\s+(.+?)(?:['’]s)?\s+profile"
    if visible.casefold() not in generic and not re.fullmatch(generic_profile, visible, flags=re.I):
        match = re.fullmatch(pattern, visible, flags=re.I)
        name = match.group(1).strip() if match else visible
        if name and len(name) <= 90 and name.casefold() not in generic:
            return name
    accessible_text = " ".join((label or "").split())
    if accessible_text.casefold() not in generic and not re.fullmatch(generic_profile, accessible_text, flags=re.I):
        accessible = re.fullmatch(pattern, accessible_text, flags=re.I)
        if accessible:
            name = accessible.group(1).strip()
            if name and len(name) <= 90 and name.casefold() not in generic:
                return name
    return None


def names_match(discovered: str, displayed: str) -> bool:
    """Compare one person's name without mistaking credentials for identity."""
    def normalized(value):
        value = (value or "").strip()
        # LinkedIn result links sometimes append a connection badge. Never
        # mistake it for a part of the member's name.
        value = re.split(r"\s+[·•]\s+(?=(?:1st|2nd|3rd|premium|verified)\b)",
                         value, maxsplit=1, flags=re.I)[0]
        value = re.sub(
            r"\s*\((?:he|him|she|her|they|them)(?:\s*/\s*(?:he|him|she|her|they|them))+\)\s*$",
            "", value, flags=re.I)
        # Only remove unambiguous professional credentials. Keep Jr./Sr. and
        # numerals; they distinguish real people with otherwise equal names.
        credential = r"(?:esq\.?|esquire|j\.?\s*d\.?|ll\.?\s*m\.?|m\.?\s*b\.?\s*a\.?|ph\.?\s*d\.?|m\.?\s*d\.?|cpa|pmp)"
        value = re.sub(r"(?:\s*,?\s+" + credential + r")+\s*$", "", value, flags=re.I)
        value = unicodedata.normalize("NFKD", value).casefold()
        value = "".join(c for c in value if not unicodedata.combining(c))
        words = re.findall(r"[^\W_]+", value, flags=re.UNICODE)
        prefixes = {"assemblywoman", "assemblyman", "senator", "representative",
                    "congresswoman", "congressman", "dr", "professor", "hon", "honorable"}
        while len(words) >= 3 and words[0] in prefixes:
            words.pop(0)
        return words

    expected, actual = normalized(discovered), normalized(displayed)
    if not expected or not actual:
        return False
    if expected == actual:
        return True
    # Expand a middle initial only when *both* names give one: Sarah E.
    # Jones and Sarah Elizabeth Jones can be the same person, but Sarah
    # Jones and Sarah E. Jones remain ambiguous and must not be guessed.
    if len(expected) != len(actual) or len(expected) < 3:
        return False
    if expected[0] != actual[0] or expected[-1] != actual[-1]:
        return False
    return all(a == b or (min(len(a), len(b)) == 1 and a[0] == b[0])
               for a, b in zip(expected[1:-1], actual[1:-1]))


def match_score(snippet: str, terms: list[str] | None = None) -> int:
    """Local heuristic, not an AI judgment. Must match at least one interest term."""
    words = (snippet or "").casefold()
    hits = sum(1 for term in (DEFAULT_MATCH_TERMS if terms is None else terms) if term.strip() and term.strip().casefold() in words)
    return min(100, hits * 25)


def has_stop_signal(text: str) -> bool:
    s = (text or "").casefold()
    return any(phrase in s for phrase in STOP_PHRASES)


def local_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(TZ)).astimezone(TZ)


def in_work_hours(now: datetime, start: int, end: int) -> bool:
    d = local_now(now)
    return start <= d.hour < end


def seconds_until_work_hours(now: datetime, start: int, end: int) -> int:
    d = local_now(now)
    if in_work_hours(d, start, end):
        return 0
    next_day = d.date() if d.hour < start else (d + timedelta(days=1)).date()
    next_start = datetime.combine(next_day, time(start, 0), TZ)
    return max(0, math.ceil((next_start.astimezone(timezone.utc) - d.astimezone(timezone.utc)).total_seconds()))


def seconds_until_next_send(now: datetime, last_sent_iso: str | None, cooldown_seconds: float) -> float:
    """Return remaining seconds with sub-second precision."""
    if not last_sent_iso:
        return 0
    last = datetime.fromisoformat(last_sent_iso)
    if last.tzinfo is None:
        raise ValueError("Stored timestamps require a timezone")
    elapsed = (now.astimezone(timezone.utc) - last.astimezone(timezone.utc)).total_seconds()
    return max(0.0, cooldown_seconds - elapsed)


def seconds_until_tomorrow(now: datetime) -> int:
    d = local_now(now)
    nxt = datetime.combine((d + timedelta(days=1)).date(), time(0, 0), TZ)
    return max(0, math.ceil((nxt.astimezone(timezone.utc) - d.astimezone(timezone.utc)).total_seconds()))


# Optional strict keyword filters on visible profile text. Each field accepts
# alternatives (OR); nonempty fields must ALL match (AND). These are not
# verified LinkedIn metadata fields; missing information is a non-match.
FILTER_FIELDS = ("locations", "titles", "companies", "schools", "industries")


def clean_terms(value) -> list[str]:
    """Accept a list or comma-separated text. Case-insensitive, no empty filters."""
    if isinstance(value, str):
        value = value.split(",")
    return list(dict.fromkeys(x.strip().casefold() for x in (value or []) if isinstance(x, str) and x.strip()))


def passes_profile_filters(profile_text: str, header_text: str, filters: dict | None,
                           match_terms: list[str] | None = None) -> tuple[bool, str]:
    """Strictly require visible evidence for every enabled filter group."""
    f = filters or {}
    text = " ".join((profile_text or "").casefold().split())
    if not text:
        return False, "Profile text unavailable; cannot verify filters."
    threshold = int(f.get("min_score", 0))
    if match_score(text, match_terms) < threshold:
        return False, "Profile below the minimum interest match score."
    header = " ".join((header_text or "").casefold().split())
    for field in FILTER_FIELDS:
        terms = clean_terms(f.get(field))
        # Check location and role in the person's introduction, not references
        # to earlier employers, schools, or unrelated people later on the page.
        source = header if field in ("locations", "titles") else text
        if terms and not any(term in source for term in terms):
            return False, f"No visible match for {field} filter."
    excluded = clean_terms(f.get("exclude"))
    if any(term in text for term in excluded):
        return False, "Profile contains an excluded keyword."
    degree = str(f.get("degree", "Any"))
    if degree != "Any":
        # Inspect intro/header only; don't mistake other members in recommendations.
        allowed = ("2nd",) if degree == "2nd only" else ("2nd", "3rd")
        if not any(re.search(r"\b" + x + r"\b", header) for x in allowed):
            return False, "Connection degree not confirmed in profile header."
    return True, "Visible profile matches enabled filters."


def safe_saved_int(value, default: int, minimum: int, maximum: int) -> int:
    """Recover from malformed or out-of-range saved UI values at startup."""
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if minimum <= parsed <= maximum else default


MIN_COOLDOWN_SECONDS = 0.1
MAX_COOLDOWN_SECONDS = 6 * 3600


def valid_cooldown_seconds(value) -> bool:
    """Reject non-finite or out-of-range intervals, including user-entered NaN."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(seconds) and MIN_COOLDOWN_SECONDS <= seconds <= MAX_COOLDOWN_SECONDS


def theoretical_daily_capacity(cooldown_seconds: float, start_hour=SEND_START_HOUR, end_hour=SEND_END_HOUR) -> int:
    """Arithmetic pacing upper bound only; browsing takes additional time."""
    total_seconds = (end_hour - start_hour) * 3600
    if total_seconds <= 0 or not valid_cooldown_seconds(cooldown_seconds):
        return 0
    return math.ceil(total_seconds / cooldown_seconds)
