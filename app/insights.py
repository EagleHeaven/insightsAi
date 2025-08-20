from typing import List, Dict
import numpy as np
from collections import Counter
from datetime import datetime
from app.llm import ask_llm
import html
import logging
import re

logger = logging.getLogger("insights-logic")

def _sanitize(text: str) -> str:
    """Sanitize text to prevent prompt injection, XSS, and block unwanted code."""
    text = str(text)
    # Remove non-printable and unsafe characters; hyphen at end to avoid range issues
    text = re.sub(r"[^\x20-\x7EÀ-ÿ’€.,;:!?()\[\]\\'\"%$@-]", "", text)
    # Remove code fences
    text = text.replace("```", "")
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Escape for safe HTML output
    return html.escape(text)

def generate_insights(reviews: List[Dict[str, str]]) -> dict:
    """
    reviews = [ {"text": "...", "source": "Google", "date": "2024-08-15"}, ... ]
    Only reviews with a parsable date are retained. If the reviews come exclusively
    from the Google API, limit to the 5 most recent.
    """
    filtered = []
    for r in reviews:
        txt = _sanitize(r.get("text", "").strip())
        src = _sanitize(r.get("source", "Unknown").strip())
        date_str = r.get("date", "").strip()
        if not txt or not date_str:
            continue
        try:
            _ = datetime.strptime(date_str, "%Y-%m-%d")
            filtered.append({"text": txt, "source": src, "date": date_str})
        except ValueError:
            logger.warning(f"Invalid date format: {date_str}")
            continue

    if not filtered:
        return {
            "review_count": 0,
            "summary": "No valid reviews found.",
            "llm_report": "",
            "period": "—",
            "source": {}
        }

    is_google_api_data = all(r["source"] == "Google" for r in filtered)
    filtered_sorted = sorted(
        filtered,
        key=lambda r: datetime.strptime(r["date"], "%Y-%m-%d"),
        reverse=True
    )
    if is_google_api_data:
        filtered_sorted = filtered_sorted[:5]

    texts, sources = zip(*[(r["text"], r["source"]) for r in filtered_sorted])
    total_reviews = len(texts)
    start_date = filtered_sorted[-1]["date"]
    end_date = filtered_sorted[0]["date"]
    period = f"{start_date} → {end_date}"

    src_counter = Counter(sources)
    prefix_map = {"Google": "G", "Tripadvisor": "TA", "Booking": "BK"}
    src_line = " | ".join(
        f"{k} ({prefix_map.get(k, k[:2].upper())}) {v}" for k, v in src_counter.items()
    )

    corpus = []
    for txt, src in zip(texts, sources):
        prefix = prefix_map.get(src, src[:2].upper())
        corpus.append(f"{prefix}>> {_sanitize(txt)}")
    joined = "\n".join(corpus)
    src_header = f"Sources: {src_line}"

    prompt = f"""
SYSTEM:
You are Insights, a senior CX analyst. STRICTLY follow the format below (dates like 01 Apr → 31 May 2025 should reflect the actual period).
Begin with “Overall Sentiment: Positive”, “Overall Sentiment: Negative” or “Overall Sentiment: Mixed sentiment” based on all reviews.

Then, choose your section heading as follows:
– If Positive sentiment: title it “Key strengths”
– If Negative sentiment: title it “Main issues”
– If Mixed sentiment: title it “Key observations”

ALL OUTPUT MUST BE IN FRENCH.

DEBUT DU RAPPORT
{src_header}
📊 {{SECTION_TITLE}} — {period} | {total_reviews} reviews
1️⃣ …
2️⃣ …
3️⃣ …

## KPIs à suivre
- …

## Conclusion
…
FIN DU RAPPORT

STRICT RULES
– Use the exact section titles as instructed above.
– Translate any non-French quotes into French.
– Group similar complaints into one item.
– For each item, output:
  “<Title> — <total mentions> mention(s)”.
  (Use ‘mention’ if exactly 1, otherwise ‘mentions’.)
– Include a representative quote (6–15 words) + date.
– Then Root cause + Action (1 line each).
– If the issue relates to staff, coordination or wait time:
  ➤ Consider implementing fivo.be to solve this type of problem.
– Keep the report ≤300 words.
– Abbreviations: G=Google · TA=Tripadvisor · BK=Booking.

CORPUS:
{joined}
"""

    try:
        report = ask_llm(prompt)
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        report = "Analysis failed due to an internal error."

    return {
        "review_count": total_reviews,
        "summary": "Sentiment handled by LLM.",
        "period": period,
        "source": dict(src_counter),
        "llm_report": report.strip()
    }