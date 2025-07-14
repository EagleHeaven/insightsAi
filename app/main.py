# app/main.py

from fastapi import FastAPI, HTTPException, File, UploadFile, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd, io, os, logging
from datetime import datetime
from dateutil.relativedelta import relativedelta
from app.reviews import fetch_google_reviews
from app.insights import generate_insights
 
# --- LOGGING (audit/monitoring, OWASP requirement) ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("insights")

# --- SECURITY: Rate limiting per IP (DoS basic mitigation) ---
limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return PlainTextResponse("Too Many Requests - Only 4 allowed per hour", status_code=429)

# --- CORS: Only allow your ALB (edit for custom domain if needed) ---
ALLOWED_ORIGINS = [
    "https://insightsai.vibeconnect.be"
    # add your domain here later
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Security headers compatible with your HTML ---
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    # Autorise les scripts inline JS de ta page (sinon tes boutons runaway et scroll ne marchent plus)
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline';"
    return response

# --- Templates & static assets ---
templates = Jinja2Templates(directory="templates")
if os.path.isdir("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# --- HELPERS ---
def parse_relative_date(text: str) -> str:
    now = datetime.now()
    text = text.lower()
    if "mois" in text:
        try:
            nb = int(''.join(c for c in text if c.isdigit()))
            return (now - relativedelta(months=nb)).strftime("%Y-%m-%d")
        except Exception as e:
            logger.warning(f"parse_relative_date failed (mois): {text} - {e}")
            return ""
    if "an" in text:
        try:
            nb = int(''.join(c for c in text if c.isdigit()))
            return (now - relativedelta(years=nb)).strftime("%Y-%m-%d")
        except Exception as e:
            logger.warning(f"parse_relative_date failed (an): {text} - {e}")
            return ""
    return ""

# --- PAGE HTML ---
@app.get("/", response_class=HTMLResponse)
@limiter.limit("30/minute")  # Optionnel : homepage peut rester + permissive
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- 1- CSV upload ---
@app.post("/insights/csv", response_class=JSONResponse)
@limiter.limit("4/hour")  # Nouvelle limite stricte
async def insights_csv(request: Request, file: UploadFile = File(...)):
    # Only allow .csv and .txt uploads
    if not file.filename.lower().endswith(('.csv', '.txt')):
        logger.warning(f"Rejected file upload: {file.filename}")
        raise HTTPException(415, "Only CSV files are allowed.")
    try:
        content = await file.read()
        df = pd.read_csv(
            io.StringIO(content.decode("utf-8")),
            sep=None,
            engine="python",
            on_bad_lines="skip"
        )
    except Exception as e:
        logger.error(f"CSV upload failed: {e}")
        raise HTTPException(400, "Invalid CSV file.")
    if len(df) > 10000:
        raise HTTPException(413, "CSV file too large.")

    try:
        text_col = next(c for c in df.columns if "text" in c.lower() or "review" in c.lower())
    except StopIteration:
        raise HTTPException(400, "No column containing review text found.")
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
        raise HTTPException(400, "No valid reviews found.")

    return JSONResponse(generate_insights(records))

# --- 2- Google Places ---
@app.post("/insights/google", response_class=JSONResponse)
@limiter.limit("4/hour")  # Nouvelle limite stricte
async def insights_google(request: Request,
    name: str = Form(..., description="Nom de l’établissement"),
    city: str = Form(..., description="Ville")
):
    if not (1 <= len(name) <= 120) or not (1 <= len(city) <= 80):
        raise HTTPException(400, "Invalid input values.")
    try:
        reviews = fetch_google_reviews(name=name.strip(), city=city.strip())
    except ValueError as e:
        logger.warning(f"Google fetch ValueError: {e}")
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Google fetch error: {e}")
        raise HTTPException(500, "Internal error when fetching reviews.")
    return JSONResponse(generate_insights(reviews))

# --- ERROR HANDLER: Don't expose stacktrace in prod! ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error."}
    )