# app/services/report_generator.py
from __future__ import annotations

from typing import Any
from html import escape
import json
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return escape(str(v))

def _as_iter(x: Any) -> list[Any]:
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]

def _pick(d: dict[str, Any], *names: str, fallback: Any = None) -> Any:
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return fallback

def _issue_svg() -> str:
    # simple SF-like bullet (sparkle-ish)
    return (
        '<svg width="14" height="14" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" aria-hidden="true">'
        '<path d="M12 3l2 4 4 2-4 2-2 4-2-4-4-2 4-2 2-4z"></path>'
        '</svg>'
    )

def _dept_badge(dept: str) -> str:
    if not dept:
        return ""
    def _route_icon(name: str) -> str:
        key = (name or "").strip().lower()
        if "front" in key:
            return "🛎️"
        if "house" in key:
            return "🧹"
        if "maint" in key or "repair" in key:
            return "🛠️"
        if "manage" in key:
            return "🏛️"
        if "dining" in key or "beverage" in key or "food" in key or "f&" in key:
            return "🍽️"
        if "service" in key:
            return "🤝"
        return "🏷️"
    icon = _route_icon(dept)
    return f'<span class="rpt-badge">{icon} {_safe_str(dept)}</span>'

# ---------------------------------------------------------------------------
# Prompt builder + Normalization
# ---------------------------------------------------------------------------

def build_llm_prompt_from_reviews(hotel: str, city: str, reviews: list[dict[str, Any]]) -> str:
    """
    Construit un prompt clair pour GPT à partir des avis collectés.
    On force un schéma JSON précis dans la réponse pour faciliter la normalisation.
    """
    hotel = str(hotel or "").strip()
    city = str(city or "").strip()

    # on compresse les avis en texte simple (limite raisonnable)
    lines: list[str] = []
    for r in reviews[:120]:
        if not isinstance(r, dict):
            continue
        src = str(r.get("source", "") or "")[:40]
        dt = str(r.get("date", "") or "")
        rating = r.get("rating")
        raw_txt = str(r.get("text", "") or "").replace("\n", " ").strip()
        txt = (raw_txt[:280] + "…") if len(raw_txt) > 280 else raw_txt
        if rating is None:
            head = f"[{src}] {dt}".strip()
        else:
            head = f"[{src}] {dt} ★{rating}".strip()
        if txt:
            lines.append(f"- {head}: {txt}")
    corpus = "\n".join(lines) if lines else "- (no usable reviews fetched)"

    schema = """
You must respond with ONE valid JSON object (no additional text).
Expected schema (exact keys):
{
  "title": string,
  "issue_distribution": [
    { "label": string, "percent": number, "route_to": string }
  ],
  "key_observations": [
    {
      "title": string,
      "quote": { "text": string, "source": string, "date": string },
      "route_to": string,
      "mentions": number
    }
  ],
  "kpis_to_monitor": [string],
  "conclusion": string,
  "sources": [ { "name": string, "count": number } ]
}
Constraints:
- Limit "issue_distribution" to the top 5 items. "percent" must be an integer between 0 and 100.
- "route_to" must be an internal team (e.g., "Front Desk", "Housekeeping", "Maintenance", "Dining & Beverages", "Management").
- "key_observations": max 5 entries, "mentions" = approximate count of occurrences.
- "sources": group by platform (e.g., Google, Blogs).
"""

    instructions = f"""
Hospitality context:
- Property: "{hotel}" in "{city}"
- Data: recent public guest reviews (sample)

Task:
- Analyze the reviews to surface the top issues distribution, 3-5 key observations with short quotes, KPIs to monitor, and a conclusion.
- Follow the JSON schema and constraints strictly.
- Write every field in fluent English (even if the review snippets are in another language).

Reviews (sample):
{corpus}

{schema}
Return only the JSON object with no extra text.
"""
    return instructions.strip()


def _coerce_int(v: Any, min_v: int = 0, max_v: int = 100) -> int:
    try:
        # accepte "42", 42.7, etc.
        n = int(round(float(str(v).strip())))
    except Exception:
        n = 0
    if n < min_v:
        n = min_v
    if n > max_v:
        n = max_v
    return n


def normalize_report_json(ai_json: Any) -> dict[str, Any]:
    """
    Normalise la sortie (dict ou JSON str) du modèle en structure interne
    attendue par build_preview_html / build_pdf_html.
    """
    # 1) parse si str
    def _extract_json_object(s: str) -> Any:
        try:
            return json.loads(s)
        except Exception:
            pass
        # Try to salvage first balanced JSON object
        # Basic brace matching ignoring braces inside simple quotes
        start = s.find('{')
        while start != -1:
            depth = 0
            in_str = False
            esc = False
            for i, ch in enumerate(s[start:], start):
                if in_str:
                    if esc:
                        esc = False
                    elif ch == '\\':
                        esc = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            frag = s[start:i+1]
                            try:
                                return json.loads(frag)
                            except Exception:
                                break
            start = s.find('{', start + 1)
        return {}

    if isinstance(ai_json, str):
        ai: Any = _extract_json_object(ai_json)
    elif isinstance(ai_json, dict):
        ai = dict(ai_json)  # shallow copy
    else:
        ai = {}

    out: dict[str, Any] = {
        "title": "Hospitality Insights Report",
        "issue_distribution": [],
        "key_observations": [],
        "kpis_to_monitor": [],
        "conclusion": "",
        "sources": []
    }

    # 2) title
    t = ai.get("title") if isinstance(ai, dict) else None
    if isinstance(t, str) and t.strip():
        out["title"] = t.strip()

    # 3) issues
    issues_in = []
    if isinstance(ai, dict):
        for key in ("issue_distribution", "issues", "top_issues"):
            v = ai.get(key)
            if isinstance(v, list):
                issues_in = v
                break

    issues_out: list[dict[str, Any]] = []
    for it in issues_in[:5]:
        if not isinstance(it, dict):
            # autorise format simplifié: string
            if isinstance(it, str) and it.strip():
                issues_out.append({"label": it.strip(), "percent": 0, "route_to": ""})
            continue
        label = it.get("label") or it.get("title") or it.get("name") or "Issue"
        percent = it.get("percent") or it.get("percentage") or it.get("share") or 0
        dept = it.get("route_to") or it.get("department") or it.get("dept") or ""
        issues_out.append({
            "label": str(label).strip(),
            "percent": _coerce_int(percent, 0, 100),
            "route_to": str(dept).strip()
        })
    out["issue_distribution"] = issues_out

    # 4) observations
    obs_in = []
    if isinstance(ai, dict):
        for key in ("key_observations", "observations"):
            v = ai.get(key)
            if isinstance(v, list):
                obs_in = v
                break

    obs_out: list[dict[str, Any]] = []
    for ob in obs_in[:5]:
        if not isinstance(ob, dict):
            continue
        title_ob = ob.get("title") or ob.get("name") or "Observation"
        dept = ob.get("route_to") or ob.get("department") or ""
        mentions = ob.get("mentions") or ob.get("count") or 0

        quote_data = ob.get("quote")
        if isinstance(quote_data, dict):
            q_text = str(quote_data.get("text") or quote_data.get("example") or "").strip()
            q_src = str(quote_data.get("source") or quote_data.get("src") or "").strip()
            q_date = str(quote_data.get("date") or quote_data.get("when") or "").strip()
        else:
            # parfois "quote" peut être une simple chaîne
            q_text = str(ob.get("quote") or ob.get("example") or "").strip()
            q_src = str(ob.get("source") or ob.get("src") or "").strip()
            q_date = str(ob.get("date") or ob.get("when") or "").strip()

        obs_out.append({
            "title": str(title_ob).strip(),
            "quote": {"text": q_text, "source": q_src, "date": q_date},
            "route_to": str(dept).strip(),
            "mentions": _coerce_int(mentions, 0, 9999)
        })
    out["key_observations"] = obs_out

    # 5) KPIs
    kpis_in = []
    if isinstance(ai, dict):
        for key in ("kpis_to_monitor", "kpis", "metrics"):
            v = ai.get(key)
            if isinstance(v, list):
                kpis_in = v
                break
    kpis_out: list[str] = []
    for k in kpis_in[:12]:
        if isinstance(k, (str, int, float)):
            kpis_out.append(str(k).strip())
    out["kpis_to_monitor"] = kpis_out

    # 6) conclusion
    concl = ""
    if isinstance(ai, dict):
        for key in ("conclusion", "summary", "closing"):
            v = ai.get(key)
            if isinstance(v, str) and v.strip():
                concl = v.strip()
                break
    out["conclusion"] = concl

    # 7) sources
    src_in = []
    if isinstance(ai, dict):
        for key in ("sources", "review_sources"):
            v = ai.get(key)
            if isinstance(v, list):
                src_in = v
                break
    src_out: list[dict[str, Any]] = []
    for s in src_in[:12]:
        if isinstance(s, dict):
            name = s.get("name") or s.get("label") or "Source"
            count = s.get("count") or s.get("n") or 0
            src_out.append({"name": str(name).strip(), "count": _coerce_int(count, 0, 999999)})
        elif isinstance(s, str):
            src_out.append({"name": s.strip(), "count": 0})
    out["sources"] = src_out

    return out

# ---------------------------------------------------------------------------
# Core renderers
# ---------------------------------------------------------------------------

def build_preview_html(normalized: dict[str, Any]) -> str:
    """
    Construit un fragment HTML (aperçu) à partir de la structure normalisée.
    Le HTML retourné est auto-contenu (styles minimaux inclus) et sûr (escape).
    """
    # CORRECTION: Validation des données d'entrée
    if not isinstance(normalized, dict):
        normalized = {}

    meta_info = _pick(normalized, "meta", fallback={})
    if not isinstance(meta_info, dict):
        meta_info = {}
    prop_name = _safe_str(meta_info.get("property_name", ""))
    city_meta = _safe_str(meta_info.get("city", ""))

    raw_title_val = _pick(normalized, "title", "report_title", fallback="See Insights in action")
    raw_title = _safe_str(str(raw_title_val))

    def _dedup_title(text: str) -> str:
        if not text:
            return "Hospitality Insights Report"
        parts = [p.strip() for p in text.split("—") if p.strip()]
        cleaned: list[str] = []
        prop_lower = prop_name.lower()
        combo_lower = f"{prop_name}, {city_meta}".lower() if prop_name and city_meta else ""
        for idx, part in enumerate(parts):
            part_lower = part.lower()
            if idx > 0 and prop_lower and part_lower == prop_lower:
                continue
            if idx > 0 and combo_lower and part_lower == combo_lower:
                continue
            cleaned.append(part)
        return " — ".join(cleaned) if cleaned else text

    title = _dedup_title(raw_title)

    # CORRECTION: Extraction plus robuste des données
    issues: list[dict[str, Any]] = _pick(
        normalized, "issue_distribution", "issues", "top_issues", fallback=[]
    ) or []
    if not isinstance(issues, list):
        issues = []
        
    observations: list[dict[str, Any]] = _pick(
        normalized, "key_observations", "observations", fallback=[]
    ) or []
    if not isinstance(observations, list):
        observations = []
        
    kpis: list[str] = _pick(normalized, "kpis_to_monitor", "kpis", fallback=[]) or []
    if not isinstance(kpis, list):
        kpis = []
        
    conclusion: str = _pick(normalized, "conclusion", "summary", fallback="") or ""

    # sources (optionnel)
    sources = _as_iter(_pick(normalized, "sources", "review_sources", fallback=[]))

    route_map = {
        "front desk": "Front Desk",
        "frontdesk": "Front Desk",
        "housekeeping": "Housekeeping",
        "maintenance": "Maintenance",
        "management": "Management",
        "f&b": "Dining & Beverages",
        "f&amp;b": "Dining & Beverages",
        "food": "Dining & Beverages",
        "dining": "Dining & Beverages",
        "service": "Guest Services",
    }

    def _route_display(value: str) -> str:
        key = (value or "").strip().lower()
        return route_map.get(key, (value or "").strip())

    def _issue_emoji(percent: int) -> str:
        if percent >= 40:
            return "🚨"
        if percent >= 25:
            return "⚠️"
        if percent >= 12:
            return "😟"
        return "🙂"

    def _issue_colors(percent: int) -> tuple[str, str]:
        if percent >= 40:
            return ("rgba(244,63,94,0.18)", "linear-gradient(90deg,#f43f5e 0%,#fb7185 100%)")
        if percent >= 25:
            return ("rgba(249,115,22,0.18)", "linear-gradient(90deg,#f97316 0%,#fb923c 100%)")
        if percent >= 12:
            return ("rgba(250,204,21,0.22)", "linear-gradient(90deg,#facc15 0%,#fde047 100%)")
        return ("rgba(34,197,94,0.18)", "linear-gradient(90deg,#22c55e 0%,#4ade80 100%)")

    # --- Issue distribution (top 5)
    issues_html = []
    for it in issues[:5]:
        if isinstance(it, dict):
            label = _safe_str(_pick(it, "label", "title", "name", fallback="Issue"))
            pct_val = _coerce_int(_pick(it, "percent", "ratio", "share", fallback=0), 0, 100)
            dept_raw = _pick(it, "route_to", "department", "dept", fallback="")
            dept = _safe_str(_route_display(dept_raw))
        else:
            label = _safe_str(str(it))
            pct_val = 0
            dept = ""
        badge = _dept_badge(dept) if dept else ""
        emoji = _issue_emoji(pct_val)
        track_bg, fill_bg = _issue_colors(pct_val)
        issues_html.append(
            "<li class='rpt-issue-item'>"
            f"<div class='rpt-issue-icon' style='background:{track_bg}'>{emoji}</div>"
            "<div class='rpt-issue-content'>"
            f"<div class='rpt-issue-meta'><span class='rpt-issue-label'>{label}</span>{badge}</div>"
            f"<div class='rpt-progress' style='background:{track_bg}'><span class='rpt-progress-bar' style='width:{pct_val}%;background:{fill_bg}'></span></div>"
            "</div>"
            f"<span class='rpt-issue-percent'>{pct_val}%</span>"
            "</li>"
        )
    issues_block = (
        "<section class='rpt-card rpt-card--issues'>"
        "<h3 class='rpt-card-title'>Issue distribution</h3>"
        + ("<ul class='rpt-issue-list'>" + ("".join(issues_html) or "<li class='rpt-empty'>No issues detected.</li>") + "</ul>")
        + "</section>"
    )

    # --- Key observations
    obs_html = []
    for idx, ob in enumerate(observations[:5], start=1):
        if not isinstance(ob, dict):
            continue

        title_ob = _safe_str(_pick(ob, "title", "name", fallback="Observation"))

        quote_data = ob.get("quote")
        if isinstance(quote_data, dict):
            quote = _safe_str(_pick(quote_data, "text", "example", fallback=""))
            src = _safe_str(_pick(quote_data, "source", "src", fallback=""))
            date = _safe_str(_pick(quote_data, "date", "when", fallback=""))
        else:
            quote = _safe_str(_pick(ob, "quote", "example", fallback=""))
            src = _safe_str(_pick(ob, "source", "src", fallback=""))
            date = _safe_str(_pick(ob, "date", "when", fallback=""))

        dept = _safe_str(_route_display(_pick(ob, "route_to", "department", fallback="")))
        mentions = _safe_str(_pick(ob, "mentions", "count", fallback=""))

        meta_bits = []
        if mentions:
            meta_bits.append(f"{mentions} mention(s)")
        if src:
            meta_bits.append(src)
        if date:
            meta_bits.append(date[:10])
        meta = " · ".join([m for m in meta_bits if m])

        badge = _dept_badge(dept) if dept else ""

        obs_html.append(
            "<li class='rpt-ob'>"
            f"<span class='rpt-ob-index'>{idx}</span>"
            f"<div class='rpt-ob-title'>{title_ob}{badge}</div>"
            + (f"<blockquote class='rpt-ob-quote'>“{quote}”</blockquote>" if quote else "")
            + (f"<div class='rpt-ob-meta'>{meta}</div>" if meta else "")
            + "</li>"
        )
    obs_block = (
        "<section class='rpt-card rpt-card--observations'>"
        "<h3 class='rpt-card-title'>Key observations</h3>"
        + ("<ol class='rpt-obs'>" + ("".join(obs_html) or "<li class='rpt-empty'>No observations available.</li>") + "</ol>")
        + "</section>"
    )

    # --- KPIs to monitor
    kpi_html = []
    for k in kpis[:8]:
        kpi_html.append(f"<li class='rpt-kpi'>{_safe_str(k)}</li>")
    kpi_block = (
        "<section class='rpt-card rpt-card--kpis'>"
        "<h3 class='rpt-card-title'>KPIs to monitor</h3>"
        + ("<ul class='rpt-kpis'>" + ("".join(kpi_html) or "<li class='rpt-empty'>No KPIs suggested.</li>") + "</ul>")
        + "</section>"
    )

    # --- Conclusion
    concl_block = (
        "<section class='rpt-card rpt-card--conclusion'>"
        "<h3 class='rpt-card-title'>Conclusion</h3>"
        f"<p class='rpt-text'>{_safe_str(conclusion) or '—'}</p>"
        "</section>"
    )

    # --- Sources (optionnel)
    if sources:
        src_items = []
        for s in sources:
            if isinstance(s, dict):
                name = _safe_str(_pick(s, "name", "label", fallback="Source"))
                count = _safe_str(_pick(s, "count", "n", fallback=""))
                tag = f"{name}" + (f" ({count})" if count else "")
            else:
                tag = _safe_str(str(s))
            src_items.append(f"<li>{tag}</li>")
        src_block = (
            "<section class='rpt-card rpt-card--sources'>"
            "<h3 class='rpt-card-title'>Sources</h3>"
            f"<ul class='rpt-sources-list'>{''.join(src_items)}</ul>"
            "</section>"
        )
    else:
        src_block = ""

    # --- Styles (scopés au conteneur .rpt)
    styles = """
<style>
.rpt { --glass-bg:rgba(255,255,255,0.32); --glass-border:rgba(255,255,255,0.55); --text:#0b1420; --muted:rgba(15,23,42,0.62); --shadow:0 40px 140px -60px rgba(15,23,42,0.48); font-family:'SF Pro Display','SF Pro Text','Open Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,'Helvetica Neue',Arial,sans-serif; color:var(--text); padding:48px; border-radius:40px; max-width:820px; margin:12px auto 32px; background:linear-gradient(158deg, rgba(232,240,255,0.92) 0%, rgba(247,244,255,0.85) 45%, rgba(229,242,255,0.9) 100%); box-shadow:var(--shadow); border:1px solid rgba(255,255,255,0.4); }
.rpt * { box-sizing:border-box; }
.rpt-hero { display:flex; flex-direction:column; gap:16px; margin-bottom:28px; }
.rpt-print-header { display:none; }
.rpt-hero--compact { gap:10px; margin-bottom:16px; }
.rpt-hero-top { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.rpt-brand { display:inline-flex; align-items:center; gap:10px; padding:6px 16px; border-radius:999px; font-size:12px; letter-spacing:0.16em; text-transform:uppercase; font-weight:600; color:rgba(255,255,255,0.85); background:linear-gradient(120deg, rgba(74,108,247,0.62), rgba(103,232,249,0.52)); backdrop-filter:blur(22px); box-shadow:0 12px 28px -16px rgba(52,84,209,0.55); border:1px solid rgba(255,255,255,0.35); }
.rpt-brand-icon { display:inline-flex; width:18px; height:18px; align-items:center; justify-content:center; }
.rpt-title { font-size:30px; font-weight:700; letter-spacing:-0.015em; margin:0; color:#0b1420; }
.rpt-subtitle { font-size:15px; color:var(--muted); margin:0; }
/* chips removed from header to keep only brand + preview */
.rpt-tag { display:inline-flex; align-items:center; gap:6px; padding:6px 14px; border-radius:999px; font-size:12px; font-weight:600; color:#2450ff; background:rgba(36,80,255,0.15); backdrop-filter:blur(18px); border:1px solid rgba(36,80,255,0.24); }
.rpt-cards { display:flex; flex-direction:column; gap:20px; }
.rpt-card { background:var(--glass-bg); border-radius:26px; padding:22px 26px; border:1px solid var(--glass-border); backdrop-filter:blur(28px); box-shadow:0 32px 80px -58px rgba(15,23,42,0.4); }
.rpt-card-title { font-size:16px; font-weight:700; margin:0 0 16px; letter-spacing:-0.01em; color:#101828; display:flex; align-items:center; gap:10px; }
.rpt-card-title::before { content:''; width:10px; height:10px; border-radius:50%; background:linear-gradient(135deg,#4f46e5,#22d3ee); box-shadow:0 0 0 5px rgba(79,70,229,0.12); }
.rpt-issue-list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:16px; }
.rpt-issue-item { display:grid; grid-template-columns:48px 1fr auto; align-items:center; gap:16px; }
.rpt-issue-icon { font-size:24px; display:flex; align-items:center; justify-content:center; border-radius:18px; width:48px; height:48px; color:#0f172a; box-shadow:inset 0 8px 16px rgba(255,255,255,0.35); backdrop-filter:blur(12px); }
.rpt-issue-content { display:flex; flex-direction:column; gap:8px; }
.rpt-issue-meta { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.rpt-issue-label { font-weight:600; color:var(--text); font-size:15px; }
.rpt-issue-percent { font-variant-numeric:tabular-nums; font-weight:700; color:#0f172a; font-size:15px; }
.rpt-progress { grid-column:1 / -1; height:12px; width:100%; border-radius:999px; overflow:hidden; margin:0; background:rgba(255,255,255,0.32); border:1px solid rgba(255,255,255,0.45); }
.rpt-progress-bar { display:block; height:100%; border-radius:999px; box-shadow:0 8px 14px -8px rgba(15,23,42,0.35); }
.rpt-badge { margin-left:8px; padding:5px 12px; border-radius:999px; font-size:11.5px; font-weight:600; color:#12263b; background:rgba(255,255,255,0.65); border:1px solid rgba(255,255,255,0.45); backdrop-filter:blur(14px); }
.rpt-obs { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:16px; }
.rpt-ob { position:relative; padding:20px 20px 20px 72px; border-radius:22px; background:rgba(255,255,255,0.6); border:1px solid rgba(255,255,255,0.48); backdrop-filter:blur(24px); box-shadow:0 24px 52px -48px rgba(15,23,42,0.45); }
.rpt-ob-index { position:absolute; left:22px; top:20px; width:32px; height:32px; border-radius:12px; background:linear-gradient(135deg,#3b82f6,#60a5fa); color:#fff; font-weight:700; font-variant-numeric:tabular-nums; display:flex; align-items:center; justify-content:center; box-shadow:0 10px 24px -16px rgba(59,130,246,0.8); }
.rpt-ob-title { font-weight:600; color:var(--text); display:flex; align-items:center; gap:10px; margin-bottom:10px; font-size:15px; }
.rpt-ob-quote { margin:0; font-size:14.5px; color:#16213e; border-left:3px solid rgba(59,130,246,0.35); padding-left:14px; line-height:1.68; }
.rpt-ob-meta { margin-top:10px; font-size:12.5px; color:var(--muted); }
.rpt-kpis { list-style:none; margin:0; padding:0; display:grid; gap:12px; }
.rpt-kpi { position:relative; padding:10px 14px 10px 26px; font-size:14.5px; color:#0f172a; background:rgba(255,255,255,0.55); border-radius:16px; border:1px solid rgba(255,255,255,0.5); box-shadow:0 16px 40px -30px rgba(15,23,42,0.4); backdrop-filter:blur(18px); }
.rpt-kpi::before { content:''; width:8px; height:8px; border-radius:50%; background:linear-gradient(135deg,#4f46e5,#22d3ee); position:absolute; left:12px; top:50%; transform:translateY(-50%); box-shadow:0 0 0 6px rgba(79,70,229,0.18); }
.rpt-text { margin:0; font-size:14.5px; line-height:1.75; color:#14213d; }
.rpt-sources-list { list-style:none; margin:0; padding:0; display:grid; gap:8px; font-size:14px; color:#14213d; }
.rpt-sources-list li { position:relative; padding-left:18px; }
.rpt-sources-list li::before { content:''; position:absolute; left:6px; top:50%; transform:translateY(-50%); width:6px; height:6px; border-radius:50%; background:linear-gradient(135deg,#4f46e5,#22d3ee); }
.rpt-empty { font-size:13px; color:var(--muted); padding:2px 0; }
@media (max-width:720px) {
  .rpt { padding:32px; border-radius:34px; }
  .rpt-card { padding:20px; border-radius:22px; }
  .rpt-title { font-size:26px; }
  .rpt-issue-item { grid-template-columns:44px 1fr auto; }
  .rpt-issue-icon { width:44px; height:44px; }
}
</style>
""".strip()

    subtitle_bits = []
    if prop_name:
        subtitle_bits.append(prop_name)
    if city_meta and city_meta not in prop_name:
        subtitle_bits.append(city_meta)
    total_reviews_obj: Any = meta_info.get("total_reviews")
    total_reviews_int: int
    if isinstance(total_reviews_obj, (int, float, str)):
        try:
            total_reviews_int = int(round(float(str(total_reviews_obj).strip())))
        except Exception:
            total_reviews_int = 0
    else:
        total_reviews_int = 0
    if total_reviews_int:
        subtitle_bits.append(f"{total_reviews_int} public reviews")
    generated_at = _safe_str(meta_info.get("generated_at", ""))
    if generated_at:
        subtitle_bits.append(generated_at)
    subtitle_line = " · ".join([s for s in subtitle_bits if s]) or "Generated from recent guest feedback."

    # Full header with title and meta as requested
    # Title: Guest Review Analysis
    h1_title = "Guest Review Analysis"
    # Subline: Property · City · N public reviews · Date
    sub_bits = []
    if prop_name:
        sub_bits.append(prop_name)
    if city_meta:
        sub_bits.append(city_meta)
    if total_reviews_int:
        sub_bits.append(f"{total_reviews_int} public reviews")
    if generated_at:
        sub_bits.append(generated_at)
    sub_line = " · ".join([s for s in sub_bits if s])

    # Chips row mirroring the subline with icons
    chips: list[str] = []
    if prop_name:
        chips.append(f"<li class='rpt-chip'>🏨 {prop_name}</li>")
    if city_meta:
        chips.append(f"<li class='rpt-chip'>📍 {city_meta}</li>")
    if total_reviews_int:
        chips.append(f"<li class='rpt-chip'>💬 {total_reviews_int} reviews</li>")
    if generated_at:
        chips.append(f"<li class='rpt-chip'>📅 {generated_at}</li>")
    chips_html = f"<ul class='rpt-chips'>{''.join(chips)}</ul>" if chips else ''

    header = (
        "<header class='rpt-hero'>"
        "<div class='rpt-hero-top'>"
        "<div class='rpt-brand'><span class='rpt-brand-icon'>💡</span>InsightsAI</div>"
        + ("<span class='rpt-tag'>Preview</span>" if True else "")
        + "</div>"
        f"<h1 class='rpt-title'>{h1_title}</h1>"
        + (f"<p class='rpt-subtitle'>{_safe_str(sub_line)}</p>" if sub_line else "")
        + chips_html
        + "</header>"
    )

    html = (
        f"{styles}"
        "<section class='rpt'>"
        f"{header}"
        "<div class='rpt-cards'>"
        f"{issues_block}"
        f"{obs_block}"
        f"{kpi_block}"
        f"{concl_block}"
        f"{src_block}"
        "</div>"
        "</section>"
    )
    return html


def build_pdf_html(normalized: dict[str, Any]) -> str:
    """
    Construit un document HTML complet pour WeasyPrint à partir du même contenu
    que l’aperçu. On encapsule le fragment dans une page A4.
    """
    body = build_preview_html(normalized)
    # Strip inline preview <style> to avoid overriding print CSS and causing overflows
    try:
        body = re.sub(r"<style>.*?</style>", "", body, flags=re.S)
    except Exception:
        pass
    print_css = """
@page { size: A4; margin: 5mm; @top-center { content: element(pageHeader) } }
html, body {
  background:#fff; margin:0; padding:0;
  font-family:'SF Pro Text','Open Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:7.2px; line-height:1.18; color:#101828;
}
/* Single page: keep everything within the page content box */
.rpt {
  padding:0 !important; border-radius:0 !important; max-width:100% !important; margin:0 !important;
  box-shadow:none !important; border:none !important; background:#fff !important;
  /* Let content flow but we cap visible items and minimize spacing to fit */
  page-break-inside: auto; break-inside: auto;
}
/* Allow normal breaking inside content to avoid forced extra pages */
.rpt * { page-break-inside: auto !important; break-inside: auto !important; }
/* Running header content */
.rpt-print-header { position: running(pageHeader); }
.rpt-ph-title { font-weight:700; font-size:8.5px; }
.rpt-ph-sub { font-size:7px; color:#5b6475; }

/* Header (super compact) */
.rpt-hero { margin:0 !important; gap:1px !important; padding:0 0 !important; }
.rpt-brand, .rpt-tag, .rpt-chips { display:none !important; }
.rpt-title { font-size:12px !important; letter-spacing:-0.01em !important; margin:0 0 2px 0 !important; }
.rpt-subtitle { font-size:7px !important; color:#5b6475 !important; margin:0 0 3px 0 !important; }

/* Cards (tight) */
.rpt-cards { gap:2px !important; padding:0 !important; }
.rpt-card { padding:3px 4px !important; border-radius:4px !important;
  border:1px solid rgba(15,23,42,0.08) !important; box-shadow:none !important; background:#f8f9fb !important;
}
.rpt-card { page-break-inside:auto !important; }
.rpt-card-title { font-size:8px !important; margin:0 0 2px 0 !important; text-transform:uppercase; letter-spacing:0.12em; color:#0f172a !important; }

/* Issues (3 items max) */
.rpt-issue-list { gap:2px !important; margin:0 !important; padding:0 !important; }
.rpt-issue-item { display:grid !important; grid-template-columns:16px 1fr auto !important; align-items:center !important; gap:4px !important; }
.rpt-issue-icon { width:14px !important; height:14px !important; font-size:9px !important; border-radius:3px !important; background:#e5e7ef !important; display:flex !important; align-items:center !important; justify-content:center !important; }
.rpt-issue-content { display:flex !important; flex-direction:column !important; gap:1px !important; }
.rpt-issue-meta { display:flex !important; align-items:center !important; gap:4px !important; }
.rpt-issue-label { font-size:8px !important; font-weight:600 !important; }
.rpt-issue-percent { font-size:8px !important; font-weight:600 !important; }
.rpt-progress { display:none !important; }
.rpt-badge { padding:0 3px !important; font-size:7px !important; border-radius:999px !important; background:rgba(15,23,42,0.08) !important; color:#0f172a !important; border:none !important; }

/* Observations (3 max) */
.rpt-ob { padding:4px 4px 4px 16px !important; border-radius:4px !important; background:#f5f6fa !important; border:1px solid rgba(15,23,42,0.06) !important; }
.rpt-ob-index { width:10px !important; height:10px !important; font-size:7px !important; left:4px !important; top:4px !important; }
.rpt-ob-title { font-size:8px !important; margin:0 0 2px 0 !important; }
.rpt-ob-quote { font-size:8px !important; max-height:1.8em !important; overflow:hidden !important; line-height:1.25 !important; border-left:2px solid rgba(15,23,42,0.12) !important; padding-left:6px !important; }
.rpt-ob-meta { font-size:7px !important; margin-top:2px !important; color:#5b6475 !important; }

/* KPIs (hidden in print to guarantee 1 page) */
.rpt-kpis { gap:2px !important; }
.rpt-kpi { padding:2px 4px 2px 10px !important; font-size:8px !important; border-radius:4px !important; background:#f5f6fa !important; border:1px solid rgba(15,23,42,0.06) !important; }
.rpt-kpi::before { width:3px !important; height:3px !important; left:5px !important; }

/* Text */
.rpt-text { font-size:8px !important; line-height:1.3 !important; color:#0f172a !important; }
.rpt-sources-list { font-size:7.2px !important; }

/* Hard limits of visible items to guarantee one A4 page */
.rpt-issue-list > li:nth-child(n+4) { display:none !important; }
.rpt-obs > li:nth-child(n+4) { display:none !important; }
.rpt-kpis > li:nth-child(n+4) { display:none !important; }
.rpt-card--sources .rpt-sources-list > li:nth-child(n+4) { display:none !important; }
/* Only keep the requested sections in print */
.rpt-card--kpis, .rpt-card--conclusion { display:none !important; }
"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>InsightsAI Report</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>{print_css}</style>
</head>
<body>
  {body}
</body>
</html>
""".strip()

__all__ = ["build_llm_prompt_from_reviews", "normalize_report_json", "build_preview_html", "build_pdf_html"]
