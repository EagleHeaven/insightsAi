# app/main.py
from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import pandas as pd, io, os
from datetime import datetime
from dateutil.relativedelta import relativedelta
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from os import getenv

# Load environment variables early so services pick up .env values
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()  # loads from .env if present
except Exception:
    # If python-dotenv isn't installed, ignore; env may be provided by the process
    pass

from app.services import gpt_service as _gpt
from app.utils import insights as _insights
from typing import Any, Awaitable, Callable, Optional, cast

# ---------------------------------------------------------------------------
# Compatibility wrapper: expose gpt5_analyze() even if the concrete function
# in app.services.gpt_service has a different name.
# ---------------------------------------------------------------------------
async def gpt5_analyze(prompt: str) -> dict[str, Any]:
    """
    Proxy to the first analyzer available in app.services.gpt_service.
    Returns a dict containing at least a 'content' key.
    This uses getattr() to satisfy static analysis (Pylance) and runtime safety.
    """
    analyzer: Optional[Callable[[str], Awaitable[dict[str, Any]]]] = None
    for name in ("gpt5_analyze", "analyze_with_gpt5", "analyze_report"):
        func = getattr(_gpt, name, None)
        if callable(func):
            analyzer = func  # type: ignore[assignment]
            break
    if analyzer is None:
        raise RuntimeError("No analyzer function found in app.services.gpt_service")
    return await analyzer(prompt)

# ---------------------------------------------------------------------------
# Compatibility shim: normalize_report_json
# ---------------------------------------------------------------------------
def normalize_report_json(data: dict[str, Any]) -> dict[str, Any]:
    """
    Try to call whichever normalization function exists in app.utils.insights.
    Falls back to a minimal pass-through structure if nothing matches.
    """
    for name in ("normalize_report_json", "normalize_report", "normalize", "to_normalized_report", "to_json"):
        fn = getattr(_insights, name, None)
        if callable(fn):
            try:
                out = fn(data)  # type: ignore[call-arg]
                if isinstance(out, dict):
                    return out
            except Exception:
                # If the candidate function fails, try the next one.
                pass

    # Fallback: preserve shape so downstream code/templates don't crash
    if isinstance(data, dict):
        return data
    return {"llm_text": str(data)}

app = FastAPI()

# CORS — autoriser localhost et l'origine publique (prod) + .env overrides
ALLOWED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
_public_origin = getenv("PUBLIC_ORIGIN")
if _public_origin:
    ALLOWED_ORIGINS.append(_public_origin)

# Optional: comma-separated list in .env (ALLOWED_ORIGINS), accepts "*" to allow all (no credentials)
_env_origins = getenv("ALLOWED_ORIGINS")
if _env_origins:
    if _env_origins.strip() == "*":
        ALLOWED_ORIGINS = ["*"]
    else:
        ALLOWED_ORIGINS.extend([o.strip() for o in _env_origins.split(",") if o.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
    max_age=600,
)

@app.middleware("http")
async def security_headers(request, call_next):
    resp: Response = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = (
        "geolocation=(), microphone=(), camera=(), payment=()"
    )
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    # CSP (autorise nos propres scripts/styles/images et l'API OpenAI)
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "script-src 'self'; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self' https://api.openai.com; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    resp.headers["Cache-Control"] = "no-store"
    if request.url.scheme == "https":
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return resp

from fastapi import APIRouter
from app.routes import report_routes as _report_routes  # type: ignore

# Resolve the APIRouter symbol robustly (supports router/report_router/api_router)
_report_router_obj = (
    getattr(_report_routes, "router", None)
    or getattr(_report_routes, "report_router", None)
    or getattr(_report_routes, "api_router", None)
)
if _report_router_obj is None:
    raise RuntimeError("No APIRouter found in app.routes.report_routes")
report_router: APIRouter = cast(APIRouter, _report_router_obj)
# The report router exposes /api/report and /api/report/pdf
app.include_router(report_router)

# templates et assets - CORRECTION
templates_dir = "templates" if os.path.isdir("templates") else "app/templates"
templates = Jinja2Templates(directory=templates_dir)

# CORRECTION: Montage des fichiers statiques plus robuste
static_dirs = ["static", "app/static"]
for static_dir in static_dirs:
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        break
else:
    # Fallback: créer un dossier static vide si aucun n'existe
    os.makedirs("static", exist_ok=True)
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/favicon.ico")
async def favicon():
    # No favicon file; return 204 to silence 404s in logs.
    return Response(status_code=204)

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
def parse_relative_date(text: str) -> str:
    now = datetime.now()
    text = text.lower()
    if "mois" in text:
        try:
            nb = int(''.join(c for c in text if c.isdigit()))
            return (now - relativedelta(months=nb)).strftime("%Y-%m-%d")
        except:
            return ""
    if "an" in text:
        try:
            nb = int(''.join(c for c in text if c.isdigit()))
            return (now - relativedelta(years=nb)).strftime("%Y-%m-%d")
        except:
            return ""
    return ""

# ---------------------------------------------------------------------------
# Helper to build LLM prompt from reviews
# ---------------------------------------------------------------------------
def build_llm_prompt_from_reviews(reviews: list[dict]) -> str:
    # Construit un prompt compact et sécurisé pour l'IA à partir des avis fournis
    # On tronque à 40 avis maxi pour éviter les prompts énormes
    rows = []
    for r in reviews[:40]:
        text = str(r.get("text", "")).replace("\n", " ").strip()
        if not text:
            continue
        src = (r.get("source") or "").upper()[:1] or "G"
        dt  = str(r.get("date") or "").strip()[:10]
        rows.append(f'- "{text[:300]}" ({src}, {dt})')
    header = (
        "Generate an Apple-like hospitality insights report with these public reviews.\n"
        "Return concise, neutral insights. Sections: Issue distribution (top 5 with %),\n"
        "Key observations (3–5) each with ONE short quote + (SRC, YYYY-MM-DD) and route_to dept,\n"
        "KPI baseline (optional), KPI to monitor (3–6), Conclusion. No PII. No code.\n\n"
        "Reviews:\n"
    )
    return header + "\n".join(rows)

# ---------------------------------------------------------------------------
# PAGE HTML
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    Home page: front-end demo (How it works + See Insights in action).
    """
    return templates.TemplateResponse("index.html", {"request": request})


# ---------------------------------------------------------------------------
# 1️⃣  CSV upload
# ---------------------------------------------------------------------------
@app.post("/insights/csv", response_class=JSONResponse)
async def insights_csv(file: UploadFile = File(...)):
    """
    Génère un rapport Echo depuis un CSV d'avis.
    Détecte automatiquement séparateur & colonnes texte / source.
    """
    try:
        content = await file.read()
        df = pd.read_csv(
            io.StringIO(content.decode("utf-8")),
            sep=None,
            engine="python",
            on_bad_lines="skip"
        )
    except Exception as e:
        raise HTTPException(400, f"Could not parse CSV: {e}")

    # Détection des colonnes
    cols = [c.lower() for c in df.columns]
    try:
        text_col = next(c for c in df.columns if "text" in c.lower() or "review" in c.lower())
    except StopIteration:
        raise HTTPException(400, "Aucune colonne contenant le texte d'avis.")
    try:
        source_col = next(c for c in df.columns if "source" in c.lower() or "platform" in c.lower())
    except StopIteration:
        source_col = None
    try:
        date_col = next(c for c in df.columns if "date" in c.lower())
    except StopIteration:
        date_col = None

    records = [
        {
            "text": str(row[text_col]).strip(),
            "source": str(row[source_col]).strip() if source_col else "csv",
            "date": parse_relative_date(str(row[date_col])) if date_col else ""
        }
        for _, row in df.iterrows() if str(row[text_col]).strip()
    ]
    if not records:
        raise HTTPException(400, "Aucun avis valide trouvé.")

    # Génération du rapport via GPT puis normalisation
    prompt = build_llm_prompt_from_reviews(records)
    ai = await gpt5_analyze(prompt)
    data = {"llm_text": ai.get("content", ""), "meta": {"total_reviews": len(records)}}
    normalized = normalize_report_json(data)
    return JSONResponse(normalized)


# ---------------------------------------------------------------------------
# 2️⃣  Recherche web (GPT‑5)
# ---------------------------------------------------------------------------
@app.post("/insights/google", response_class=JSONResponse)
async def insights_google(
    name: str = Form(..., description="Nom de l’établissement"),
    city: str = Form(..., description="Ville")
):
    """
    Agrège les avis publics Google Places via GPT‑5 browsing (expérimental), puis génère le rapport.
    """
    try:
        browse_obj = getattr(_gpt, "gpt5_browse_reviews", None)
        if browse_obj is None or not callable(browse_obj):
            raise RuntimeError("gpt5_browse_reviews not available in gpt_service")
        # Help static typing (Pylance): declare the expected async callable signature.
        browse_func = cast(Callable[..., Awaitable[list[dict]]], browse_obj)
        reviews = await browse_func(name=name, city=city, max_reviews=20)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erreur collecte web: {e}")

    # Génération du rapport via GPT puis normalisation
    prompt = build_llm_prompt_from_reviews(reviews)
    ai = await gpt5_analyze(prompt)
    data = {"llm_text": ai.get("content", ""), "meta": {"total_reviews": len(reviews)}}
    normalized = normalize_report_json(data)
    return JSONResponse(normalized)

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health", response_class=JSONResponse)
async def health():
    return {"status": "ok"}

# Helpful for debugging front/back alignment of endpoints
@app.get("/api/config", response_class=JSONResponse)
async def api_config():
    return {"endpoints": {"generate": "/api/report", "pdf": "/api/report/pdf"}}
