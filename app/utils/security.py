# app/utils/security.py
from __future__ import annotations

import html
import re
import unicodedata
from typing import Final
from urllib.parse import urlparse

# --- Character policies -------------------------------------------------------
# Allow: latin letters (incl. accents), digits, space, dash, apostrophe,
# dot, ampersand, comma, parentheses, slash.
# (Good coverage for hotel/restaurant names and cities.)
_ALLOWED_CHARS_PATTERN: Final[str] = r"0-9A-Za-z\s\-\.'&,&/()\u00C0-\u017F"

# Remove anything not in the allowlist
SAFE_RE: Final[re.Pattern[str]] = re.compile(fr"[^{_ALLOWED_CHARS_PATTERN}]")
# Collapse whitespace
WS_RE: Final[re.Pattern[str]] = re.compile(r"\s+")

# First char must be a letter/digit, total length 2..120
NAME_RE: Final[re.Pattern[str]] = re.compile(
    fr"^[0-9A-Za-z\u00C0-\u017F][{_ALLOWED_CHARS_PATTERN}]{{1,119}}$"
)

ALLOWED_SCHEMES: Final[set[str]] = {"http", "https", "mailto", "tel"}


def _nfkc(s: str) -> str:
    """Unicode normalize to NFKC to reduce homoglyph / confusable issues."""
    return unicodedata.normalize("NFKC", s)


def collapse_ws(s: str) -> str:
    """Collapse any run of whitespace to a single space and trim."""
    return WS_RE.sub(" ", s).strip()


def sanitize_text(s: str | None, max_len: int = 160) -> str:
    """
    Generic sanitizer for short text fields:
    - NFKC normalization
    - Remove characters outside our allowlist
    - Collapse whitespace
    - Clip to max_len
    Returns "" on None/empty input.
    """
    if not s:
        return ""
    s = _nfkc(s)
    s = SAFE_RE.sub("", s)
    s = collapse_ws(s)
    if max_len > 0:
        s = s[:max_len]
    return s


def sanitize_name(s: str | None, max_len: int = 120) -> str:
    """
    Sanitize names (hotel, restaurant, city).
    Returns a clipped, allowlisted string (may be empty if everything was stripped).
    """
    return sanitize_text(s or "", max_len=max_len)


def assert_valid_name(s: str, field: str = "value") -> str:
    """
    Validate a sanitized name. Raises ValueError if too short or contains
    unsupported characters.
    """
    if not s or len(s) < 2:
        raise ValueError(f"{field} is too short")
    if not NAME_RE.match(s):
        raise ValueError(f"{field} contains unsupported characters")
    return s


def escape_html(s: str) -> str:
    """HTML-escape for safe rendering in templates (defense-in-depth)."""
    return html.escape(s, quote=True)


def safe_url(u: str | None) -> str:
    """
    Whitelist URL schemes to prevent javascript: or data: issues.
    Returns "" if URL is unsafe or empty.
    """
    if not u:
        return ""
    u = _nfkc(u.strip())
    try:
        parsed = urlparse(u)
    except Exception:
        return ""
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return ""
    return u