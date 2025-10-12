# app/utils/robots.py
from __future__ import annotations

import os
import time
from typing import Dict, Tuple, Optional
from urllib.parse import urlparse

import httpx
from urllib import robotparser

# ---------------------------------------------------------------------------
# Config via ENV
# ---------------------------------------------------------------------------
RESPECT_ROBOTS: bool = os.getenv("RESPECT_ROBOTS", "true").lower() == "true"
ROBOTS_CACHE_TTL_SECONDS: int = int(os.getenv("ROBOTS_CACHE_TTL_SECONDS", "1800"))  # 30 min
ROBOTS_FAIL_OPEN: bool = os.getenv("ROBOTS_FAIL_OPEN", "true").lower() == "true"   # si robots.txt indisponible
CRAWLER_USER_AGENT: str = os.getenv(
    "CRAWLER_USER_AGENT",
    "InsightsAIBot/1.0 (+https://example.com/bot)"
)

_HTTP_TIMEOUT = float(os.getenv("ROBOTS_HTTP_TIMEOUT_SECONDS", "8"))

# Cache: origin -> (timestamp, RobotFileParser)
_CACHE: Dict[str, Tuple[float, robotparser.RobotFileParser]] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _origin_from_url(url: str) -> Optional[str]:
    try:
        p = urlparse(url)
        if not p.scheme or not p.netloc:
            return None
        # garde le port si présent
        netloc = p.netloc
        return f"{p.scheme}://{netloc}"
    except Exception:
        return None


def _fetch_robots_text(origin: str) -> Optional[str]:
    """
    Récupère le contenu de {origin}/robots.txt (HTTP bloquant, simple et robuste).
    """
    robots_url = f"{origin.rstrip('/')}/robots.txt"
    try:
        headers = {"User-Agent": CRAWLER_USER_AGENT, "Accept": "text/plain,*/*"}
        r = httpx.get(robots_url, headers=headers, timeout=_HTTP_TIMEOUT, follow_redirects=True)
        # 2xx = OK, 404 = pas de robots (fail-open selon config)
        if r.status_code == 404:
            return ""  # signifie "aucune règle" ; décision en aval
        r.raise_for_status()
        return r.text or ""
    except httpx.HTTPError:
        return None


def _parse_robots(origin: str, text: str) -> robotparser.RobotFileParser:
    rp = robotparser.RobotFileParser()
    rp.set_url(f"{origin.rstrip('/')}/robots.txt")
    # robotparser.parse attend une liste de lignes
    rp.parse(text.splitlines() if text else [])
    return rp


def _get_parser_for_origin(origin: str) -> Optional[robotparser.RobotFileParser]:
    """
    Retourne un parser (via cache TTL). None si indisponible et fail-closed.
    """
    now = time.time()
    cached = _CACHE.get(origin)
    if cached:
        ts, rp = cached
        if now - ts < ROBOTS_CACHE_TTL_SECONDS:
            return rp

    text = _fetch_robots_text(origin)

    # Non joignable
    if text is None:
        if ROBOTS_FAIL_OPEN:
            # Pas de fichier => autoriser (parser vide)
            rp = _parse_robots(origin, "")
            _CACHE[origin] = (now, rp)
            return rp
        else:
            # Fail-closed
            return None

    # 404 ou vide => pas de règles (tout autorisé)
    rp = _parse_robots(origin, text)
    _CACHE[origin] = (now, rp)
    return rp


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------
def allowed_by_robots(url: str, user_agent: Optional[str] = None) -> bool:
    """
    Vérifie si l'URL est autorisée selon robots.txt.
    - Si RESPECT_ROBOTS=false, retourne toujours True.
    - Si robots.txt indisponible: comportement contrôlé par ROBOTS_FAIL_OPEN.
    """
    if not RESPECT_ROBOTS:
        return True

    origin = _origin_from_url(url)
    if not origin:
        # URL mal formée: on autorise pour ne pas bloquer le pipeline
        return True

    rp = _get_parser_for_origin(origin)
    if rp is None:
        # fail-closed: robots introuvable et politique stricte
        return False

    ua = (user_agent or CRAWLER_USER_AGENT or "*").strip() or "*"
    try:
        return bool(rp.can_fetch(ua, url))
    except Exception:
        # Par prudence on autorise si parser plante (évite de bloquer la démo)
        return True


def clear_robots_cache() -> None:
    """Vide le cache (utile pour tests)."""
    _CACHE.clear()


__all__ = ["allowed_by_robots", "clear_robots_cache"]