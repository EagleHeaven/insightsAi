# app/utils/pdf_utils.py
# Server-side HTML → PDF with safe defaults and engine selection via .env (PDF_ENGINE).
# Supports: WeasyPrint (preferred) or wkhtmltopdf via pdfkit. Falls back automatically.
from __future__ import annotations

import os
from typing import Optional

# Minimal print CSS (Apple-like spacing/typography) injected if caller sends a fragment.
_PRINT_CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: -apple-system, BlinkMacSystemFont, Inter, Segoe UI, Roboto, Arial, sans-serif;
  color: #111; line-height: 1.45; font-size: 12.5pt;
}
h1, h2, h3, h4 { margin: 0 0 10pt; line-height: 1.2; }
h1 { font-size: 20pt; }
h2 { font-size: 16pt; }
h3 { font-size: 13.5pt; }
p  { margin: 0 0 8pt; }
.page-break { page-break-before: always; }
"""

def _wrap_if_fragment(html: str, extra_css: Optional[str] = None) -> str:
  """
  If 'html' looks like a fragment (no <html> tag), wrap it into a minimal
  printable document with the print CSS above. Optionally include extra CSS.
  """
  if not isinstance(html, str):
    html = str(html or "")
  lower = html.lower()
  if "<html" in lower and "</html>" in lower:
    # If it's already a full document and extra css is provided, inject it before </head>.
    if extra_css:
      head_close = lower.find("</head>")
      if head_close != -1:
        return html[:head_close] + f"<style>{extra_css}</style>" + html[head_close:]
      return html
    return html
  styles = _PRINT_CSS + (f"\n{extra_css}\n" if extra_css else "")
  return (
    "<!doctype html><html><head><meta charset='utf-8'>"
    f"<style>{styles}</style>"
    "</head><body>" + html + "</body></html>"
  )

def _render_weasy(doc: str) -> bytes:
  """Render using WeasyPrint. Raises if unavailable or rendering fails."""
  try:
    from weasyprint import HTML  # type: ignore
  except ImportError as e:
    raise RuntimeError("WeasyPrint is not installed") from e
  try:
    # base_url allows relative assets (images/css) in the HTML.
    pdf = HTML(string=doc, base_url=os.getcwd()).write_pdf()
    if not isinstance(pdf, (bytes, bytearray)):
      raise RuntimeError("WeasyPrint returned no bytes")
    return bytes(pdf)
  except Exception as e:
    raise RuntimeError(f"WeasyPrint failed to render PDF: {e}") from e

def _render_pdfkit(doc: str) -> bytes:
  """Render using wkhtmltopdf via pdfkit. Raises if unavailable or rendering fails."""
  try:
    import pdfkit  # type: ignore
  except Exception as e:
    raise RuntimeError("pdfkit (wkhtmltopdf) is not installed or misconfigured") from e
  options = {
    "quiet": "",
    "page-size": "A4",
    "encoding": "UTF-8",
    "print-media-type": "",
    "margin-top": "18mm",
    "margin-right": "16mm",
    "margin-bottom": "18mm",
    "margin-left": "16mm",
  }
  try:
    pdf = pdfkit.from_string(doc, False, options=options)
    if not isinstance(pdf, (bytes, bytearray)):
      raise RuntimeError("pdfkit returned no bytes")
    return bytes(pdf)
  except Exception as e:
    raise RuntimeError(f"wkhtmltopdf/pdfkit failed to render PDF: {e}") from e

def _env_engine() -> str:
  """
  Read preferred engine from environment:
    PDF_ENGINE=weasyprint | pdfkit | auto
  Defaults to 'auto'.
  """
  return (os.getenv("PDF_ENGINE", "auto") or "auto").strip().lower()

def html_to_pdf_bytes(html: str, *, engine: Optional[str] = None, extra_css: Optional[str] = None) -> bytes:
  """
  Convert HTML (full doc or fragment) to PDF bytes.

  Args:
    html: HTML string (full document or fragment).
    engine: 'weasyprint', 'pdfkit', 'auto' or None. If None, use PDF_ENGINE env (default 'auto').
    extra_css: Optional CSS string to inject (useful for branding tweaks).

  Returns:
    PDF as bytes.

  Raises:
    RuntimeError if rendering fails or no engine is available.
  """
  doc = _wrap_if_fragment(html, extra_css=extra_css)
  choice = (engine or _env_engine())
  if choice not in ("weasyprint", "pdfkit", "auto"):
    choice = "auto"

  if choice == "weasyprint":
    return _render_weasy(doc)
  if choice == "pdfkit":
    return _render_pdfkit(doc)

  # Auto: prefer WeasyPrint, then pdfkit
  try:
    return _render_weasy(doc)
  except Exception:
    pass
  try:
    return _render_pdfkit(doc)
  except Exception as e:
    raise RuntimeError(
      "No server-side PDF engine available. Install 'weasyprint' "
      "or 'wkhtmltopdf' with the 'pdfkit' Python wrapper."
    ) from e