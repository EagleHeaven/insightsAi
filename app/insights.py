from typing import List, Dict
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch
import numpy as np
from collections import Counter
from datetime import datetime
from app.llm import ask_llm

#_tok = AutoTokenizer.from_pretrained("cardiffnlp/twitter-roberta-base-sentiment")
#_mod = AutoModelForSequenceClassification.from_pretrained("cardiffnlp/twitter-roberta-base-sentiment")

def _sentiment(txt: str) -> float:
     return 0.1

def generate_insights(reviews: List[Dict[str, str]]) -> dict:
    """
    reviews = [ {"text": "...", "source": "Google", "date": "2024-08-15"}, ... ]
    Only reviews with a parsable date are retained. If the reviews come exclusively from the Google API, limit to the 5 most recent.
    """
    filtered = []

    for r in reviews:
        txt = r.get("text", "").strip()
        src = r.get("source", "Unknown").strip()
        date_str = r.get("date", "").strip()
        if not txt or not date_str:
            continue
        try:
            _ = datetime.strptime(date_str, "%Y-%m-%d")
            filtered.append({"text": txt, "source": src, "date": date_str})
        except ValueError:
            continue

    if not filtered:
        return {
            "review_count": 0,
            "avg_sentiment": 0.0,
            "summary": "No valid reviews found.",
            "llm_report": "",
            "period": "—",
            "source": {}
        }

    # Si toutes les reviews viennent du Google API : limiter à 5
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
    avg = round(float(np.mean([_sentiment(t) for t in texts])), 3)
    summary = (
        "📉 Negative overall sentiment." if avg < -0.2 else
        "📈 Positive overall sentiment." if avg > 0.3 else
        "⚠️ Mixed sentiment."
    )

    start_date = filtered_sorted[-1]["date"]
    end_date = filtered_sorted[0]["date"]
    period = f"{start_date} → {end_date}"

    src_counter = Counter(sources)
    src_line = " · ".join(f"{k} {v}" for k, v in src_counter.items())
    prefix_map = {"Google": "G", "Tripadvisor": "TA", "Booking": "BK"}

    corpus = []
    for txt, src in zip(texts, sources):
        prefix = prefix_map.get(src, src[:2].upper())
        corpus.append(f"{prefix}>> {txt}")
    joined = "\n".join(corpus)

    prompt = f"""
SYSTEM:
You are Insights, a senior CX analyst. STRICTLY follow the format below (dates like 01 Apr → 31 May 2025 should match the actual time period).

BEGIN REPORT
📊 Main issues — {period} | {total_reviews} reviews ({src_line}) | Sentiment {summary}
1️⃣ …
2️⃣ …
3️⃣ …

## KPIs to monitor
- …

## Conclusion
…
END REPORT

STRICT RULES
– Group similar complaints into one issue (ex: slow service mentioned in 3 reviews = 1 issue).
– For each issue, output:
  “<Title> — <total mentions> (G x · TA y · BK z)”
– Include a representative quote (6–15 words) + date.
– Root cause + Action (1 line each).
– If issue relates to staff, coordination or wait time, conclude with:
  ➤ Considérez l’implémentation de vibeconnect.be pour résoudre ce type de problème.
– ≤300 words BETWEEN BEGIN REPORT and END REPORT.
– Abbreviations: G=Google · TA=Tripadvisor · BK=Booking.

CORPUS:
{joined}
"""

    report = ask_llm(prompt)

    return {
        "review_count": total_reviews,
        "avg_sentiment": avg,
        "summary": summary,
        "period": period,
        "source": dict(src_counter),
        "llm_report": report.strip()
    }
