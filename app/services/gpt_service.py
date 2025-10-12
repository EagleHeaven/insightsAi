# app/services/gpt_service.py
from __future__ import annotations

import os
import json
import asyncio
from typing import Any, Dict, Optional, List, TypedDict, Callable, Awaitable, cast
import inspect

try:
    # SDK v1+
    from openai import AsyncOpenAI
    # Common exceptions for robust retries
    from openai import (
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
    )
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "OpenAI SDK missing or incompatible. Install/upgrade with: "
        "pip install --upgrade openai"
    ) from e


# ---------------------------------------------------------------------------
# Client & config
# ---------------------------------------------------------------------------
_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not _OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set in environment")

_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
_OPENAI_MODEL_FALLBACK = os.getenv("OPENAI_MODEL_FALLBACK", "gpt-4o-mini")

# Optional per-stage models & knobs
_OPENAI_MODEL_ANALYZE = os.getenv("OPENAI_MODEL_ANALYZE", _OPENAI_MODEL)
_OPENAI_MODEL_GATHER = os.getenv("OPENAI_MODEL_GATHER", _OPENAI_MODEL)

# Reasoning / verbosity config (sanitized below)
_OPENAI_REASONING = (os.getenv("OPENAI_REASONING", "low") or "low").strip().lower()
_OPENAI_VERBOSITY = (os.getenv("OPENAI_VERBOSITY", "low") or "low").strip().lower()

# Toggle to include Responses API reasoning traces (off by default to avoid
# "Empty reasoning item" in dashboards and reduce cost). Enable with 1/true.
_OPENAI_INCLUDE_REASONING = str(os.getenv("OPENAI_INCLUDE_REASONING", "0")).strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Tokens cap for Responses API
try:
    _MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "700"))
except Exception:
    _MAX_OUTPUT_TOKENS = 700
try:
    _MAX_OUTPUT_TOKENS_FALLBACK = int(os.getenv("MAX_OUTPUT_TOKENS_FALLBACK", str(min(_MAX_OUTPUT_TOKENS, 350))))
except Exception:
    _MAX_OUTPUT_TOKENS_FALLBACK = min(_MAX_OUTPUT_TOKENS, 350)

# Concurrency limiter to avoid rate-bursts
try:
    _OPENAI_MAX_CONCURRENCY = int(os.getenv("OPENAI_MAX_CONCURRENCY", "2"))
except Exception:
    _OPENAI_MAX_CONCURRENCY = 2

# Web search knobs
# Removed:
# _USE_WEB_SEARCH = str(os.getenv("USE_WEB_SEARCH", "true")).lower() in ("1", "true", "yes", "on")
# _SEARCH_CONTEXT_SIZE = (os.getenv("SEARCH_CONTEXT_SIZE", "low") or "low").strip().lower()

_client: Optional[AsyncOpenAI] = None
_RESPONSES_SUPPORTS_RESPONSE_FORMAT: Optional[bool] = None


def _get_client() -> AsyncOpenAI:
    global _client, _RESPONSES_SUPPORTS_RESPONSE_FORMAT
    if _client is None:
        _client = AsyncOpenAI(api_key=_OPENAI_API_KEY)
        if _RESPONSES_SUPPORTS_RESPONSE_FORMAT is None:
            _RESPONSES_SUPPORTS_RESPONSE_FORMAT = _detect_response_format_support(_client)
    return _client


# Global asyncio semaphore
_SEM = asyncio.Semaphore(_OPENAI_MAX_CONCURRENCY)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# JSON schema used to strongly steer the model output
_REPORT_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "issue_distribution": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "percent": {"type": "integer", "minimum": 0, "maximum": 100},
                    "route_to": {"type": "string"}
                },
                "required": ["label", "percent", "route_to"],
                "additionalProperties": False
            }
        },
        "key_observations": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "quote": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "source": {"type": "string"},
                            "date": {"type": "string"}
                        },
                        "required": ["text", "source", "date"],
                        "additionalProperties": False
                    },
                    "route_to": {"type": "string"},
                    "mentions": {"type": "integer", "minimum": 0, "maximum": 9999}
                },
                "required": ["title", "quote", "route_to", "mentions"],
                "additionalProperties": False
            }
        },
        "kpis_to_monitor": {
            "type": "array",
            "items": {"type": "string"}
        },
        "conclusion": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "count": {"type": "integer", "minimum": 0}
                },
                "required": ["name", "count"],
                "additionalProperties": False
            }
        }
    },
    "required": [
        "title",
        "issue_distribution",
        "key_observations",
        "kpis_to_monitor",
        "conclusion",
        "sources"
    ],
    "additionalProperties": False
}

def _json_schema_response_format() -> Dict[str, Any]:
    """Build a strict JSON schema response_format for Responses API."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "HotelReport",
            "schema": _REPORT_JSON_SCHEMA,
            "strict": True,
        },
    }


def _detect_response_format_support(client: AsyncOpenAI) -> bool:
    """Return True if client.responses.create accepts response_format."""
    try:
        params = inspect.signature(client.responses.create).parameters
        return "response_format" in params
    except Exception:
        return False


def _responses_format_kwargs() -> Dict[str, Any]:
    if _RESPONSES_SUPPORTS_RESPONSE_FORMAT:
        return {"response_format": _json_schema_response_format()}
    return {}


def _looks_truncated(s: str) -> bool:
    if not isinstance(s, str):
        return True
    stripped = s.strip()
    if not stripped:
        return True
    if not stripped.endswith('}') and not stripped.endswith(']'):
        return True
    # Quick balance check
    if stripped.count('{') != stripped.count('}'):
        return True
    if stripped.count('[') != stripped.count(']'):
        return True
    return False


def _is_rate_limit_error(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError):  # type: ignore[arg-type]
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        return True
    error_obj = getattr(exc, "error", None)
    if error_obj is not None and getattr(error_obj, "code", None) == "rate_limit_exceeded":
        return True
    message = str(exc).lower()
    return "rate limit" in message or "429" in message


def _safe_extract_output_text(resp: Any) -> str:
    """
    GPT-5 Responses API returns a rich object. Prefer `.output_text`.
    Fallbacks ensure we extract usable text even if SDK shape changes.
    """
    # 1) Happy path
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    # 2) Fallback: dig into `output` list
    output = getattr(resp, "output", None)
    if isinstance(output, list):
        # Newer SDKs: items like {"type": "message", "content":[{"type":"output_text","text":"..."}]}
        for item in output:
            if isinstance(item, dict) and item.get("type") in ("message", "output_text"):
                content = item.get("content")
                if isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "output_text":
                            t = c.get("text")
                            if isinstance(t, str) and t.strip():
                                return t.strip()
                # Sometimes text sits directly on the item
                t2 = item.get("text")
                if isinstance(t2, str) and t2.strip():
                    return t2.strip()

    # 3) Try model_dump() if available (Pydantic models)
    try:
        if hasattr(resp, "model_dump"):
            d = resp.model_dump()  # type: ignore[attr-defined]
            if isinstance(d, dict):
                # common shapes
                t = d.get("output_text")
                if isinstance(t, str) and t.strip():
                    return t.strip()
                out = d.get("output")
                if isinstance(out, list):
                    for item in out:
                        if isinstance(item, dict):
                            if item.get("type") == "message":
                                content = item.get("content")
                                if isinstance(content, list):
                                    for c in content:
                                        if isinstance(c, dict) and c.get("type") == "output_text":
                                            tt = c.get("text")
                                            if isinstance(tt, str) and tt.strip():
                                                return tt.strip()
    except Exception:
        pass

    # 4) Last resort: repr()
    try:
        return str(resp)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
_REASONING_ALLOWED = {"minimal", "low", "medium", "high"}
_VERBOSITY_ALLOWED = {"low", "medium", "high"}
# Removed:
# _CTX_ALLOWED = {"low", "medium", "high"}

def _effective_reasoning(use_tools: bool) -> str:
    """
    If a future call uses tools, 'minimal' may be too low; we bump to 'low'.
    """
    r = _OPENAI_REASONING if _OPENAI_REASONING in _REASONING_ALLOWED else "low"
    if use_tools and r == "minimal":
        return "low"
    return r

def _text_cfg() -> Dict[str, str]:
    v = _OPENAI_VERBOSITY if _OPENAI_VERBOSITY in _VERBOSITY_ALLOWED else "low"
    return {"verbosity": v}

def _supports_reasoning(model: str) -> bool:
    m = (model or "").lower()
    # gpt-5 and o-series reasoning-capable models
    return ("gpt-5" in m) or ("o5" in m) or ("o3" in m)

# Removed helper function _web_tool_cfg()
# def _web_tool_cfg() -> Optional[Dict[str, Any]]:
#     if not _USE_WEB_SEARCH:
#         return None
#     cfg: Dict[str, Any] = {"type": "web_search_preview"}
#     if _SEARCH_CONTEXT_SIZE in _CTX_ALLOWED:
#         cfg["search_context_size"] = _SEARCH_CONTEXT_SIZE
#     return cfg


# ---------------------------------------------------------------------------
# Robust retry helper for rate limits / transient errors
# ---------------------------------------------------------------------------
async def _with_retries(op: Callable[[], Awaitable[Any]], *, attempts: int = 5, base_delay: float = 0.7) -> Any:
    """
    Execute an async OpenAI call with exponential backoff + jitter.
    Retries on common transient failures (429, 5xx, timeouts, connection issues).
    """
    for attempt in range(attempts):
        try:
            return await op()
        except (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError) as e:
            # Exponential backoff with light jitter
            delay = base_delay * (2 ** attempt) + (0.15 * (attempt + 1))
            # Cap the delay to something reasonable (e.g., 8s)
            if delay > 8:
                delay = 8
            await asyncio.sleep(delay)
            continue
    # If all retries failed, run one last time to surface the exception context
    return await op()


async def _chat_json_fallback(prompt: str, *, max_tokens: int) -> str:
    """Force JSON output using Chat Completions with response_format=json_object.
    Used when Responses API output is empty or not JSON.
    """
    client = _get_client()

    async def _op():
        return await client.chat.completions.create(
            model=_OPENAI_MODEL_FALLBACK,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a JSON formatter. Return ONLY one valid JSON object matching the user's schema. "
                        "No markdown, no code fences, no commentary."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=max_tokens,
        )

    resp = await _with_retries(_op)
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 1) Core analyzer used by the app
# ---------------------------------------------------------------------------
async def gpt5_analyze(prompt: str) -> Dict[str, Any]:
    """
    Analyze a prepared prompt and return {"content": "..."}.
    Uses GPT-5 via the Responses API (no web search, just summarization).
    """
    client = _get_client()
    # Respect concurrency & retries. No tools here.
    async with _SEM:
        try:
            kwargs: Dict[str, Any] = {
                "model": _OPENAI_MODEL_ANALYZE,
                "input": prompt,
                "text": cast(Any, _text_cfg()),
                "max_output_tokens": _MAX_OUTPUT_TOKENS,
            }
            # Enforce strict JSON object matching our schema when supported
            kwargs.update(_responses_format_kwargs())
            if _OPENAI_INCLUDE_REASONING and _supports_reasoning(_OPENAI_MODEL_ANALYZE):
                kwargs["reasoning"] = cast(Any, {"effort": _effective_reasoning(use_tools=False)})
            resp = await _with_retries(lambda: client.responses.create(**kwargs))
        except Exception as exc:
            if not _is_rate_limit_error(exc):
                raise
            # Fallback to lighter model with fewer tokens
            fb_kwargs: Dict[str, Any] = {
                "model": _OPENAI_MODEL_FALLBACK,
                "input": prompt,
                "text": cast(Any, _text_cfg()),
                "max_output_tokens": _MAX_OUTPUT_TOKENS_FALLBACK,
            }
            fb_kwargs.update(_responses_format_kwargs())
            # Only include reasoning if the fallback supports it
            if _OPENAI_INCLUDE_REASONING and _supports_reasoning(_OPENAI_MODEL_FALLBACK):
                fb_kwargs["reasoning"] = cast(Any, {"effort": "minimal"})
            resp = await _with_retries(lambda: client.responses.create(**fb_kwargs))
    content = _safe_extract_output_text(resp)
    # If output isn't valid JSON, try a chat fallback enforcing JSON
    needs_json = True
    if isinstance(content, str) and content.strip():
        s = content.strip()
        if s.startswith('{') and s.endswith('}'):
            needs_json = False
        else:
            try:
                json.loads(s)
                needs_json = False
            except Exception:
                needs_json = True
    truncated = _looks_truncated(content) if isinstance(content, str) else False
    if needs_json:
        try:
            max_tokens = _MAX_OUTPUT_TOKENS_FALLBACK
            if truncated:
                max_tokens = max(_MAX_OUTPUT_TOKENS_FALLBACK, min(_MAX_OUTPUT_TOKENS * 2, 1500))
            fb = await _chat_json_fallback(prompt, max_tokens=max_tokens)
            if fb:
                content = fb
        except Exception:
            pass
    return {"content": content}


async def gpt_json_analyze(prompt: str) -> Dict[str, Any]:
    """Always enforce a JSON object via Chat Completions fallback."""
    async with _SEM:
        txt = await _chat_json_fallback(prompt, max_tokens=_MAX_OUTPUT_TOKENS_FALLBACK)
    return {"content": txt}


# Aliases so that wrappers elsewhere in the codebase always find something
analyze_with_gpt5 = gpt5_analyze
analyze_report = gpt5_analyze


# ---------------------------------------------------------------------------
# 2) OPTIONAL: Web search helper (if you trigger browsing inside the LLM)
#    Keep here for future routes that want the model to fetch reviews itself.
# ---------------------------------------------------------------------------
class ReviewItem(TypedDict, total=False):
    source: str
    date: str
    rating: Optional[float]  # rating can be missing
    text: str
    url: str


async def gpt5_browse_reviews(name: str, city: str, max_reviews: int = 20) -> List[ReviewItem]:
    """
    Disabled in favor of Firecrawl-based pipeline.
    Use `app.services.firecrawl_service.search_and_extract_reviews` instead.
    """
    raise RuntimeError(
        "gpt5_browse_reviews is disabled. Use firecrawl_service.search_and_extract_reviews instead."
    )
