# app/routes/report_routes.py
import logging
import asyncio
from typing import Any, Dict
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, StringConstraints
from typing import Annotated

logger = logging.getLogger(__name__)

# Google Places-only pipeline (no SerpAPI)
from app.services.gpt_service import gpt5_analyze, gpt_json_analyze
import app.services.report_generator as report_generator
from app.utils.security import sanitize_text
try:
    # Google Places reviews (sync function executed in a thread)
    from app.reviews import fetch_google_reviews  # type: ignore
except Exception:  # pragma: no cover
    fetch_google_reviews = None  # type: ignore


def _is_rate_limit_error(exc: BaseException) -> bool:
    try:
        from openai import RateLimitError  # type: ignore
    except Exception:
        RateLimitError = tuple()  # type: ignore

    if isinstance(exc, RateLimitError):  # type: ignore[arg-type]
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        return True
    message = str(exc).lower()
    return "rate limit" in message or "429" in message


# Centralized helper for report generation
async def _generate_normalized(hotel: str, city: str) -> Dict[str, Any]:
    """
    1) Récupère des avis via Google Places.
    2) Construit un prompt avec ces avis.
    3) Analyse LLM (gpt-5) sans web_search.
    4) Normalise le JSON de sortie pour preview/PDF.
    """
    logger.info("_generate_normalized: start")
    hotel = sanitize_text(hotel).strip()
    city = sanitize_text(city).strip()

    # 1) Collecte Google Places exclusivement
    reviews: list[dict[str, Any]] = []
    if fetch_google_reviews is None:
        raise HTTPException(status_code=500, detail="Google Places review fetcher unavailable")
    try:
        logger.info("Fetching Google Places reviews for %s, %s", hotel, city)
        gp_reviews = await asyncio.to_thread(fetch_google_reviews, hotel, city)
        if isinstance(gp_reviews, list):
            reviews = gp_reviews
        logger.info("Google Places fetched %d reviews", len(reviews))
    except ValueError as e:
        logger.error("Google Places configuration error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("Google Places reviews fetch failed")
        reviews = []  # continue without blocking; LLM can still run with empty reviews

    # 2) Prompt
    try:
        build_prompt = getattr(report_generator, "build_llm_prompt_from_reviews", None)
        if not build_prompt:
            raise HTTPException(status_code=500, detail="build_llm_prompt_from_reviews() not found in report_generator")
        prompt = build_prompt(hotel, city, reviews)
    except Exception as e:
        logger.exception("Prompt build failed")
        raise HTTPException(status_code=500, detail=f"Prompt building error: {e}")

    # 3) Analyse LLM
    try:
        ai = await gpt5_analyze(prompt)
        # Accept either dict with `content` or a raw string
        if isinstance(ai, dict):
            content = str(ai.get("content", "")).strip()
        elif isinstance(ai, str):
            content = ai.strip()
        else:
            content = ""
        logger.debug("LLM content (truncated): %s", content[:400])
        if not content:
            raise HTTPException(status_code=503, detail="LLM unavailable or empty response")
    except HTTPException:
        raise
    except Exception as e:
        # Map OpenAI 429 rate limit to 429 for the API consumer if detectable
        if _is_rate_limit_error(e):
            raise HTTPException(status_code=429, detail="LLM rate limit reached. Please retry in a few seconds.")
        logger.exception("LLM analyze failed")
        raise HTTPException(status_code=502, detail=f"Error during LLM analysis: {e}")

    # 4) Normalisation
    try:
        normalize = getattr(report_generator, "normalize_report_json", None)
        if not normalize:
            raise HTTPException(status_code=500, detail="normalize_report_json() not found in report_generator")
        # The normalizer expects a JSON string or dict; first pass
        normalized = normalize(content)
        # If the structured parts are empty, try a stricter JSON pass
        def _empty_struct(n: Dict[str, Any]) -> bool:
            if not isinstance(n, dict):
                return True
            return not (n.get("issue_distribution") or n.get("key_observations") or n.get("kpis_to_monitor"))
        if _empty_struct(normalized):
            logger.info("Primary analysis yielded empty structures; retrying with JSON-enforced analyzer")
            ai2 = await gpt_json_analyze(prompt)
            content2 = ai2.get("content", "") if isinstance(ai2, dict) else str(ai2)
            if content2:
                normalized2 = normalize(content2)
                if not _empty_struct(normalized2):
                    normalized = normalized2
        # Build a clean, title-cased report title and enrich meta
        def _title_case(s: str) -> str:
            try:
                return s.strip().title()
            except Exception:
                return s

        h_disp = _title_case(hotel)
        c_disp = _title_case(city)

        base_title = normalized.get("title") or "Hospitality Insights Report"
        city_part = f", {c_disp}" if c_disp else ""
        normalized["title"] = f"{base_title} — {h_disp}{city_part}".strip(" —,")

        meta = normalized.setdefault("meta", {})
        meta["property_name"] = h_disp
        meta["city"] = c_disp
        # Raw LLM output is intentionally not persisted to keep responses light.
        # Enrich with count of fetched reviews
        try:
            meta["total_reviews"] = int(len(reviews or []))
        except Exception:
            meta["total_reviews"] = 0

        # If LLM didn't provide sources, derive simple counts by platform
        try:
            if not normalized.get("sources") and isinstance(reviews, list):
                counts: Dict[str, int] = {}
                for r in reviews:
                    src = str((r or {}).get("source") or "").strip() or "Unknown"
                    counts[src] = counts.get(src, 0) + 1
                normalized["sources"] = [{"name": k, "count": v} for k, v in counts.items()]
        except Exception:
            pass

        from datetime import date
        meta.setdefault("generated_at", date.today().isoformat())
        return normalized
    except Exception as e:
        logger.exception("Normalization failed")
        # Fallback minimal pour éviter le 502 (le front pourra au moins afficher quelque chose)
        return {
            "title": f"Hospitality Insights Report — {hotel.strip().title()}, {city.strip().title()}",
            "issues": [],
            "highlights": [],
            "kpis": [],
            "samples": reviews[:4] if isinstance(reviews, list) else [],
            "conclusion": "No structured insights extracted.",
            "raw": content,
            "meta": {"property_name": hotel, "city": city},
        }

router = APIRouter(prefix="/api", tags=["insights"])


class DemoPayload(BaseModel):
    hotel: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    city: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]


@router.get("/report")
async def insights_get(hotel: str, city: str):
    """
    GET variant for quick testing: /api/report?hotel=...&city=...
    Returns the same payload as the POST route.
    """
    logger.info(f"/api/report (GET) hotel={hotel}, city={city}")
    normalized = await _generate_normalized(hotel, city)
    try:
        to_html = getattr(report_generator, "build_preview_html", None)
        if not to_html:
            raise HTTPException(status_code=500, detail="build_preview_html() not found in report_generator")
        html = to_html(normalized)
        return JSONResponse({"html": html, "meta": normalized.get("meta", {})})
    except Exception as e:
        logger.exception("HTML preview generation failed (GET)")
        raise HTTPException(status_code=502, detail=f"Preview generation error: {e}")

@router.post("/report")
async def insights(payload: DemoPayload):
    logger.info(f"/api/report payload: hotel={payload.hotel}, city={payload.city}")
    normalized = await _generate_normalized(payload.hotel, payload.city)
    try:
        to_html = getattr(report_generator, "build_preview_html", None)
        if not to_html:
            raise HTTPException(status_code=500, detail="build_preview_html() not found in report_generator")
        html = to_html(normalized)
        return JSONResponse({"html": html, "meta": normalized.get("meta", {})})
    except Exception as e:
        logger.exception("HTML preview generation failed")
        raise HTTPException(status_code=502, detail=f"Preview generation error: {e}")


# PDF endpoint
@router.post("/report/pdf")
async def report_pdf(payload: DemoPayload):
    """Generate the same report and return an application/pdf attachment.
    Uses WeasyPrint if available. If the PDF engine is not installed, returns 503."""
    logger.info(f"/api/report/pdf payload: hotel={payload.hotel}, city={payload.city}")
    normalized = await _generate_normalized(payload.hotel, payload.city)
    try:
        pdf_html_fn = getattr(report_generator, "build_pdf_html", None)
        if not pdf_html_fn:
            raise HTTPException(status_code=500, detail="build_pdf_html() not found in report_generator")
        html_doc = pdf_html_fn(normalized)
    except Exception as e:
        logger.exception("PDF build failed")
        raise HTTPException(status_code=502, detail=f"PDF HTML generation error: {e}")

    try:
        from weasyprint import HTML  # type: ignore
        pdf_bytes = HTML(string=html_doc, base_url=".").write_pdf()
        headers = {"Content-Disposition": "attachment; filename=InsightsAI-report.pdf"}
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
    except Exception:
        logger.exception("PDF generation failed (WeasyPrint missing or error)")
        raise HTTPException(status_code=503, detail="PDF engine unavailable. Please install 'weasyprint'.")
