# app/reviews.py
"""
Routines d’import d’avis (CSV ou Google Places).
"""
from __future__ import annotations
import os, requests, time, logging, re
from typing import List, Dict
from datetime import datetime

# --------------------------------------------------------------------------- #
#  CONFIG
# --------------------------------------------------------------------------- #
GOOGLE_KEY = os.getenv("GOOGLE_PLACES_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not GOOGLE_KEY:
    raise ValueError("⚠️  GOOGLE_PLACES_API_KEY manquant dans .env")

ENDPOINT_TEXT = "https://maps.googleapis.com/maps/api/place/textsearch/json"
ENDPOINT_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"

logger = logging.getLogger("insights-reviews")

# --------------------------------------------------------------------------- #
#  HELPERS
# --------------------------------------------------------------------------- #
def _sanitize_input(val: str, field: str, max_length: int = 100) -> str:
    """Basiquement nettoie l’input utilisateur : strip, limite la taille, pas de caractères suspects."""
    val = (val or "").strip()
    if len(val) > max_length:
        logger.warning(f"{field} dépassé {max_length} chars : {val[:80]}...")
        raise ValueError(f"Input {field} too long")
    # Optionnel, rejette certains caractères bizarres qui n'ont rien à faire dans une recherche
    if re.search(r"[;<>$%{}|`]", val):
        raise ValueError(f"Invalid characters in {field}")
    return val

def _gget(url: str, **params):
    """Appel GET Google Places avec gestion d’erreurs & back-off."""
    params["key"] = GOOGLE_KEY
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.error(f"Erreur réseau Google API: {e}")
        raise RuntimeError("Connexion à Google impossible.")
    if data.get("status") == "OVER_QUERY_LIMIT":
        # Back-off simple (2 s) puis 2ᵉ essai
        time.sleep(2)
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        logger.error(f"Réponse Google API anormale : {data.get('status')} / params: {params}")
        raise RuntimeError(f"Google API error : {data.get('status')}")
    return data

# --------------------------------------------------------------------------- #
#  PUBLIC : récupérer les avis Google
# --------------------------------------------------------------------------- #
def fetch_google_reviews(name: str, city: str) -> List[Dict[str, str]]:
    """
    Recherche un établissement par *Text Search* puis télécharge jusqu’à
    5 avis via *Place Details*.
    Retour : [{'text': str, 'source': 'Google', 'date': 'YYYY-MM-DD'}]
    """
    # Sanitize input pour éviter la bidouille ou le spam
    name = _sanitize_input(name, "name", 100)
    city = _sanitize_input(city, "city", 80)

    # 1) recherche du place_id -------------------------------------------------
    query = f"{name} {city}".strip()
    res = _gget(
        ENDPOINT_TEXT,
        query=query,
        language="fr",
        region="be"
    )
    if not res["results"]:
        # second essai sans la ville (moins strict)
        res = _gget(
            ENDPOINT_TEXT,
            query=name,
            language="fr",
            region="be"
        )
        if not res["results"]:
            raise ValueError(f"Aucun établissement trouvé pour « {query} ».")

    place_id = res["results"][0].get("place_id")
    if not place_id:
        logger.error(f"Aucun place_id pour la recherche: {query}")
        raise ValueError(f"Aucun établissement trouvé pour « {query} ».")

    # 2) place details + reviews ----------------------------------------------
    details = _gget(
        ENDPOINT_DETAILS,
        place_id=place_id,
        language="fr",
        fields="review"
    )
    reviews_raw = details.get("result", {}).get("reviews", [])
    if not reviews_raw:
        raise ValueError("Aucun avis public disponible sur Google Places.")

    # Google renvoie au maximum 5 avis via Place Details
    reviews = []
    for r in reviews_raw[:5]:
        if not r.get("text") or not r.get("time"):
            continue
        # Protéger contre les caractères indésirables ou injections
        text = str(r["text"]).strip()
        text = re.sub(r"[^\x20-\x7EÀ-ÿ’€.,;:!?()\[\]\-\'\"%$@]", "", text)[:5000]
        date = datetime.utcfromtimestamp(r["time"]).strftime("%Y-%m-%d")
        reviews.append({
            "text": text,
            "source": "Google",
            "date": date
        })
    return reviews
