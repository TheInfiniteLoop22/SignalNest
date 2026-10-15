"""Structured JSON logging (docs/14 section 2).

One JSON object per line on stdout. Context (service, request_id) is attached automatically;
call sites add fields with `extra={...}`. Never log cookies, passwords, bodies or query-string
values: use `redact_url` for anything URL-shaped.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# LogRecord attributes that are plumbing, not payload.
_STD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def redact_url(url: str) -> str:
    """Drop credentials and replace query values with ***: https://h/p?token=*** ."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        query = urlencode(
            [(k, "***") for k, _ in parse_qsl(parts.query, keep_blank_values=True)], safe="*"
        )
        return urlunsplit((parts.scheme, host, parts.path, query, ""))
    except ValueError:
        return "<unparseable url>"


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        for key, value in record.__dict__.items():
            if key not in _STD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def setup_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    # Idempotent: replace only our own previous handler, leave others (e.g. test capture).
    root.handlers[:] = [h for h in root.handlers if not isinstance(h.formatter, JsonFormatter)] + [
        handler
    ]
    root.setLevel(level.upper())
    # uvicorn's own access log duplicates ours (and includes query strings).
    logging.getLogger("uvicorn.access").disabled = True
