# app/services/firecrawl_service.py
import os
import re
import asyncio
import logging
from typing import Any, Dict, List, Tuple
import httpx

logger = logging.getLogger(__name__)

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
BASE_URL = os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev")
TIMEOUT = float(os.getenv("FIRECRAWL_TIMEOUT_SECONDS", "45"))

# 🔧 Knobs – mets ces valeurs aussi dans .env si tu veux
PER_SITE_RESULTS = int(os.getenv("FIRECRAWL_PER_SITE_RESULTS", "2"))
MAX_REVIEWS      = int(os.getenv("FIRECRAWL_MAX_REVIEWS", "30"))
MAX_CONCURRENCY  = int(os.getenv("FIRECRAWL_CONCURRENCY", "1"))  # ← réduit pour éviter 429
SEARCH_BACKOFF   = float(os.getenv("FIRECRAWL_SEARCH_BACKOFF", "1.2"))  # secondes * (attempt+1)
EXTRACT_BACKOFF  = float(os.getenv("FIRECRAWL_EXTRACT_BACKOFF", "1.2"))
SCRAPE_BACKOFF   = float(os.getenv("FIRECRAWL_SCRAPE_BACKOFF", "1.2"))

HEADERS = {
    "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
    "Content-Type": "application/json",
}

# --- Ciblage des sources : on *exclut* Google & Play (trop de 400/robots).
ALLOWED_SITES: List[Tuple[str, str]] = [
    ("booking.com",     "{hotel} {city} avis site:booking.com"),
    ("tripadvisor.com", "{hotel} {city} avis site:tripadvisor.com"),
    ("hotels.com",      "{hotel} {city} reviews site:hotels.com"),
    ("yelp.com",        "{hotel} {city} reviews site:yelp.com"),
    # blogs de confiance (sans domain filter)
    ("*",               "{hotel} {city} hotel review blog"),
]

# Patterns d’URL qu’on accepte par domaine
URL_FILTERS: List[Tuple[str, re.Pattern]] = [
    ("booking.com", re.compile(r"booking\.com/.*/reviews/", re.I)),
    ("tripadvisor.com", re.compile(r"tripadvisor\.com/Hotel_Review-", re.I)),
    ("hotels.com", re.compile(r"hotels\.com/ho\d+/", re.I)),
    ("yelp.com", re.compile(r"yelp\.com/biz/", re.I)),
]

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text":   {"type": "string"},
                    "rating": {"type": "number"},
                    "date":   {"type": "string"},
                    "author": {"type": "string"},
                },
                "required": ["text"]
            }
        }
    }
}

def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()

def _domain(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]

def _keep_url(u: str) -> bool:
    d = _domain(u)
    for dom, pat in URL_FILTERS:
        if dom in d and pat.search(u):
            return True
    # blogs “*” : on accepte si “review” ou “avis” apparait dans l’URL
    if "review" in u.lower() or "avis" in u.lower():
        return True
    return False

async def _search(client: httpx.AsyncClient, query: str, limit: int) -> List[str]:
    payload = {"query": query, "limit": limit}
    for attempt in range(6):
        r = await client.post(f"{BASE_URL}/v1/search", json=payload, timeout=TIMEOUT)
        if r.status_code == 429:
            backoff = SEARCH_BACKOFF * (attempt + 1)
            logger.warning(f"Firecrawl search 429; sleeping {backoff:.1f}s…")
            await asyncio.sleep(backoff)
            continue
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            # 4xx -> inutile d’insister, 5xx -> petit retry
            if 500 <= e.response.status_code < 600:
                await asyncio.sleep(0.8)
                continue
            logger.error(f"Firecrawl search failed: {e}")
            return []
        data = r.json() or {}
        urls = [it.get("url") for it in data.get("data", []) if it.get("url")]
        return urls
    return []

async def _extract(client: httpx.AsyncClient, url: str) -> List[Dict[str, Any]]:
    payload = {"url": url, "schema": EXTRACT_SCHEMA, "use_cache": True}
    for attempt in range(5):
        r = await client.post(f"{BASE_URL}/v1/extract", json=payload, timeout=TIMEOUT)
        if r.status_code == 429:
            backoff = EXTRACT_BACKOFF * (attempt + 1)
            logger.warning(f"Firecrawl extract 429; sleeping {backoff:.1f}s… ({url})")
            await asyncio.sleep(backoff)
            continue
        if r.status_code == 400:
            logger.info(f"extract 400 on {url} – will fallback to scrape")
            return []
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if 500 <= e.response.status_code < 600:
                await asyncio.sleep(0.8)
                continue
            logger.error(f"Firecrawl extract failed [{e.response.status_code}] {url}")
            return []
        data = r.json() or {}
        items = (data.get("data") or {}).get("reviews") or []
        out = []
        for it in items:
            txt = _norm(it.get("text", ""))
            if not txt:
                continue
            out.append({
                "text": txt,
                "rating": it.get("rating"),
                "date": _norm(it.get("date", "")),
                "author": _norm(it.get("author", "")),
                "source": _domain(url),
                "url": url,
            })
        return out
    return []

async def _scrape_snippets(client: httpx.AsyncClient, url: str) -> List[Dict[str, Any]]:
    """Fallback quand extract est vide/400. On récupère le markdown et on
    prend des phrases 'review-like' (80–500 chars) comme mini-avis."""
    payload = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": True,
        "useCache": True,
        "pageOptions": {"waitFor": 1500},
    }
    for attempt in range(5):
        r = await client.post(f"{BASE_URL}/v1/scrape", json=payload, timeout=TIMEOUT)
        if r.status_code == 429:
            backoff = SCRAPE_BACKOFF * (attempt + 1)
            logger.warning(f"Firecrawl scrape 429; sleeping {backoff:.1f}s… ({url})")
            await asyncio.sleep(backoff)
            continue
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            if 500 <= e.response.status_code < 600:
                await asyncio.sleep(0.8)
                continue
            logger.error(f"Firecrawl scrape failed [{e.response.status_code}] {url}")
            return []
        data = r.json() or {}
        md = (data.get("markdown") or data.get("data", {}).get("markdown") or "")
        lines = [l.strip(" •-*") for l in (md or "").splitlines()]
        # Heuristique simple : phrases “propres”, ni trop courtes ni trop longues
        picks: List[str] = []
        for ln in lines:
            ln = _norm(ln)
            if 80 <= len(ln) <= 500 and re.search(r"[\.!\?…]$", ln):
                # éviter les menus/navigation
                if any(bad in ln.lower() for bad in ["cookie", "accept", "subscribe", "read more"]):
                    continue
                picks.append(ln)
            if len(picks) >= 6:
                break
        out = [{
            "text": p,
            "rating": None,
            "date": "",
            "author": "",
            "source": _domain(url),
            "url": url,
        } for p in picks]
        return out
    return []

def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in items:
        key = _norm(r.get("text", "")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out

async def search_and_extract_reviews(hotel: str, city: str, limit: int = MAX_REVIEWS) -> List[Dict[str, Any]]:
    """Retourne une liste de dicts: {text, rating?, date?, author?, source, url}"""
    if not FIRECRAWL_API_KEY:
        logger.warning("FIRECRAWL_API_KEY missing; returning empty list.")
        return []

    hotel_q, city_q = hotel.strip(), city.strip()
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT) as client:
        # 1) search par source
        urls: List[str] = []
        for domain, tpl in ALLOWED_SITES:
            # multi-lang: “avis”, “reviews”, “bewertungen”
            q = tpl.format(hotel=hotel_q, city=city_q)
            found = await _search(client, q, PER_SITE_RESULTS)
            if domain != "*":
                found = [u for u in found if domain in u]
            found = [u for u in found if _keep_url(u)]
            urls.extend(found)

        # dedupe urls
        uniq: List[str] = []
        seen = set()
        for u in urls:
            if u not in seen:
                uniq.append(u)
                seen.add(u)

        logger.info(f"Firecrawl URLs to process: {len(uniq)}")

        # 2) extract/scrape avec faible parallélisme
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        results: List[Dict[str, Any]] = []

        async def worker(u: str):
            async with sem:
                items = await _extract(client, u)
                if not items:
                    items = await _scrape_snippets(client, u)
                results.extend(items)

        if uniq:
            await asyncio.gather(*(asyncio.create_task(worker(u)) for u in uniq))

    reviews = _dedupe(results)
    if len(reviews) > limit:
        reviews = reviews[:limit]

    logger.info(f"Firecrawl collected {len(reviews)} snippets.")
    return reviews