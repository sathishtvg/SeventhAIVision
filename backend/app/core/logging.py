"""Structured JSON logging — Gap 27 — Seventh AI Vision.

Every service uses this module to produce machine-parseable log records that
carry a consistent set of fields (timestamp, level, service, request_id,
tenant_id, message) as a single JSON object per line.

Usage:
    from app.core.logging import configure_logging, get_logger, set_request_context

    # Once at startup (e.g. in main.py lifespan or worker main()):
    configure_logging(level="INFO", service_name="api")

    # In any module:
    log = get_logger(__name__)
    log.info("Alert created", extra={"alert_id": str(alert.id)})

    # In FastAPI middleware — set per-request context:
    set_request_context(request_id="req-uuid", tenant_id="t-uuid")
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# ── Context variables ─────────────────────────────────────────────────────────
# Stored per async task/coroutine via Python's contextvars — safe under asyncio
# without locks. Workers set these once per consumed message; the API sets them
# once per HTTP request in middleware.
_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_tenant_id_var:  ContextVar[str | None] = ContextVar("tenant_id", default=None)


def set_request_context(request_id: str | None, tenant_id: str | None = None) -> None:
    """Inject correlation IDs into the current async context."""
    _request_id_var.set(request_id)
    _tenant_id_var.set(tenant_id)


def clear_request_context() -> None:
    _request_id_var.set(None)
    _tenant_id_var.set(None)


# ── PII masking ───────────────────────────────────────────────────────────────
_PII_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),  # email
    re.compile(r"\+?[\d\s\-\(\)]{7,15}"),                               # phone
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),             # IPv4
]
_REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = frozenset({
    "password", "token", "access_token", "refresh_token",
    "secret", "api_key", "authorization",
})


def _mask_pii(text: str) -> str:
    """Apply PII redaction patterns to a rendered message string."""
    for pattern in _PII_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def _sanitize_extra(extra: dict[str, Any]) -> dict[str, Any]:
    """Remove or mask sensitive keys from the extra dict."""
    return {
        k: _REDACTED if k.lower() in _SENSITIVE_KEYS else v
        for k, v in extra.items()
    }


# ── JSON formatter ────────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    """Formats every LogRecord as a single-line JSON object.

    Required fields per logging policy (config/logging-policy.yml):
      timestamp, level, service, message, request_id, tenant_id
    """

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service = service_name

    def format(self, record: logging.LogRecord) -> str:
        # Base required fields
        record_dict: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": _mask_pii(record.getMessage()),
            "request_id": _request_id_var.get(),
            "tenant_id": _tenant_id_var.get(),
        }

        # Carry any extra= fields through (PII-sanitized)
        if record.__dict__.get("extra_fields"):
            record_dict.update(_sanitize_extra(record.__dict__["extra_fields"]))

        # Standard LogRecord extras that callers pass via extra={}
        skip = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "taskName",
            "extra_fields",
        }
        for key, val in record.__dict__.items():
            if key not in skip and not key.startswith("_"):
                if key.lower() not in _SENSITIVE_KEYS:
                    record_dict[key] = val

        if record.exc_info:
            record_dict["exception"] = self.formatException(record.exc_info)

        return json.dumps(record_dict, default=str)


# ── Public API ────────────────────────────────────────────────────────────────
_configured = False
_service_name: str = "unknown"


def configure_logging(
    level: str | None = None,
    service_name: str | None = None,
) -> None:
    """Configure root logger with JSON output. Call once at startup."""
    global _configured, _service_name

    effective_level = (
        level
        or os.environ.get("LOG_LEVEL", "INFO")
    ).upper()
    _service_name = (
        service_name
        or os.environ.get("SERVICE_NAME", "api")
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name=_service_name))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(effective_level)

    # Suppress noisy third-party loggers that would leak internal details
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger that inherits the root JSON handler."""
    if not _configured:
        configure_logging()
    return logging.getLogger(name)


# ── Uvicorn log_config dict ───────────────────────────────────────────────────
def get_uvicorn_log_config(level: str = "INFO", service_name: str = "api") -> dict:
    """Return a log_config dict for uvicorn.run() that routes through JsonFormatter."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": JsonFormatter,
                "service_name": service_name,
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            }
        },
        "loggers": {
            "uvicorn":        {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.error":  {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": level, "propagate": False},
        },
        "root": {"handlers": ["default"], "level": level},
    }
