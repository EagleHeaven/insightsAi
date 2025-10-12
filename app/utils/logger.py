

"""Centralized, safe logging helpers for InsightsAI.

- Key-value log format (Apple-like neatness in terminals)
- Request ID propagation using contextvars
- Redaction of secrets in log messages (API keys, tokens, Authorization headers)
- Single configure() entrypoint; respects LOG_LEVEL from environment
- Works with Uvicorn/FastAPI loggers
"""

from __future__ import annotations

import logging
import os
import re
import sys
import uuid
from contextvars import ContextVar
from contextlib import contextmanager
from typing import Iterator, Optional

# Context var used to enrich all logs with a request id
REQUEST_ID_VAR: ContextVar[str] = ContextVar("request_id", default="-")

# Simple redaction rules for common secrets
SENSITIVE_PATTERNS = [
    # Authorization: Bearer sk-...
    (re.compile(r"(authorization:\s*Bearer\s+)([A-Za-z0-9\-\._]+)", re.I), r"\1[REDACTED]"),
    # api_key=..., token=..., password: ..., secret: ...
    (re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*([^\s,;]+)"), r"\1=[REDACTED]"),
    # OpenAI-style keys
    (re.compile(r"sk-[A-Za-z0-9]{10,}"), "[REDACTED]"),
]


class RedactFilter(logging.Filter):
    """Redacts sensitive substrings from the final message string."""

    def filter(self, record: logging.LogRecord) -> bool:  # always keep record
        msg = record.getMessage()
        for pattern, repl in SENSITIVE_PATTERNS:
            msg = pattern.sub(repl, msg)
        # Overwrite the message with the redacted one
        record.msg = msg
        return True


class RequestIdFilter(logging.Filter):
    """Injects request_id from ContextVar into the log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID_VAR.get()
        return True


class KVFormatter(logging.Formatter):
    """Key-value single-line formatter for easy grepping / parsing.

    Example:
      ts=2025-08-23T15:12:01+0000 level=INFO logger=app request_id=abc123 msg=Server started
    """

    DEFAULT_FMT = (
        "ts=%(asctime)s level=%(levelname)s logger=%(name)s "
        "request_id=%(request_id)s msg=%(message)s"
    )
    DEFAULT_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None) -> None:
        super().__init__(fmt or self.DEFAULT_FMT, datefmt or self.DEFAULT_DATEFMT)


def _level_from_env() -> int:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    return getattr(logging, level_name, logging.INFO)


_configured = False


def configure_logging(level: Optional[int] = None) -> None:
    """Idempotent global logging configuration.

    - Sets root logger level and a single stdout handler
    - Adds RequestIdFilter and RedactFilter
    - Aligns Uvicorn/FastAPI loggers
    """
    global _configured
    if _configured:
        return

    lvl = level if level is not None else _level_from_env()

    root = logging.getLogger()
    root.setLevel(lvl)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(lvl)
    handler.setFormatter(KVFormatter())
    handler.addFilter(RequestIdFilter())
    handler.addFilter(RedactFilter())

    # Replace existing handlers to avoid duplicates when reloading
    root.handlers[:] = []
    root.addHandler(handler)

    # Keep Uvicorn/Starlette/FastAPI aligned
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        lg = logging.getLogger(name)
        lg.setLevel(lvl)
        lg.propagate = True

    _configured = True


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a configured logger. Safe to call anywhere."""
    configure_logging()
    logger = logging.getLogger(name or "app")
    # In libraries, add a NullHandler to avoid 'No handler found' warnings
    logger.addHandler(logging.NullHandler())
    return logger


@contextmanager
def request_context(request_id: Optional[str] = None) -> Iterator[None]:
    """Context manager to set a request id for all logs in the block."""
    token = REQUEST_ID_VAR.set(request_id or generate_request_id())
    try:
        yield
    finally:
        REQUEST_ID_VAR.reset(token)


def generate_request_id() -> str:
    """Generate a short, URL-safe request id."""
    return uuid.uuid4().hex[:12]


def get_request_id_from_headers(headers: dict | None) -> str:
    """Extract X-Request-ID from a headers-like mapping or create one."""
    if not headers:
        return generate_request_id()
    rid = None
    for k, v in headers.items():
        if str(k).lower() == "x-request-id":
            rid = str(v)
            break
    return rid or generate_request_id()