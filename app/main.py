# app/main.py
from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import pandas as pd, io, os
from datetime import datetime
from dateutil.relativedelta import relativedelta

from app.reviews import fetch_google_reviews        # <- seulement celui-ci
from app.insights import generate_insights

app = FastAPI()

# templates et assets
templates = Jinja2Templates(directory="templates")
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

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
# PAGE HTML
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """
    Page d'accueil : upload CSV ou recherche Google.
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

    return JSONResponse(generate_insights(records))


# ---------------------------------------------------------------------------
# 2️⃣  Recherche Google Places
# ---------------------------------------------------------------------------
@app.post("/insights/google", response_class=JSONResponse)
async def insights_google(
    name: str = Form(..., description="Nom de l’établissement"),
    city: str = Form(..., description="Ville")
):
    """
    Cherche l’établissement via Google Places API, récupère les avis disponibles,
    puis génère le rapport Echo.
    """
    try:
        reviews = fetch_google_reviews(name=name, city=city)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erreur Google Places: {e}")

    return JSONResponse(generate_insights(reviews))
