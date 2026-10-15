"""Request/response models. Validation here mirrors (and the DB CHECKs back up) docs/06 s3."""

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    IPvAnyAddress,
    field_validator,
    model_validator,
)

from app.probe.types import Assertion

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")  # RFC 7230 token
_HEADER_DENY = {
    "host",
    "authorization",
    "cookie",
    "connection",
    "content-length",
    "transfer-encoding",
}


# --- auth --------------------------------------------------------------------------------


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("not a valid email address")
        return v


class RegisterIn(Credentials):
    password: str = Field(min_length=12, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str


# --- monitors ----------------------------------------------------------------------------


def _validate_headers(headers: dict[str, str]) -> dict[str, str]:
    if len(headers) > 10:
        raise ValueError("at most 10 headers")
    for name, value in headers.items():
        if not _TOKEN.match(name):
            raise ValueError(f"invalid header name: {name!r}")
        lowered = name.lower()
        if lowered in _HEADER_DENY or lowered.startswith("proxy-"):
            raise ValueError(f"header not allowed: {name}")
        if len(value) > 512 or any(c in value for c in "\r\n"):
            raise ValueError(f"invalid value for header {name}")
    return headers


class MonitorBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "HEAD"] = "GET"
    interval_seconds: int = Field(default=60, ge=1, le=3600)
    timeout_ms: int = Field(default=5000, ge=1000, le=30000)
    headers: dict[str, str] = Field(default_factory=dict)
    assertions: list[Assertion] = Field(default_factory=list, max_length=8)
    degraded_ms: int | None = Field(default=None, ge=1)
    failure_threshold: int = Field(default=3, ge=1, le=10)
    recovery_threshold: int = Field(default=2, ge=1, le=10)

    @field_validator("headers")
    @classmethod
    def _headers(cls, v: dict[str, str]) -> dict[str, str]:
        return _validate_headers(v)

    @model_validator(mode="after")
    def _degraded(self) -> "MonitorBase":
        if self.degraded_ms is not None and self.degraded_ms >= self.timeout_ms:
            raise ValueError("degraded_ms must be lower than timeout_ms")
        return self


class MonitorCreate(MonitorBase):
    pass


class MonitorPatch(BaseModel):
    """Every field optional; unspecified fields are left unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    method: Literal["GET", "HEAD"] | None = None
    interval_seconds: int | None = Field(default=None, ge=1, le=3600)
    timeout_ms: int | None = Field(default=None, ge=1000, le=30000)
    headers: dict[str, str] | None = None
    assertions: list[Assertion] | None = Field(default=None, max_length=8)
    degraded_ms: int | None = Field(default=None, ge=1)
    failure_threshold: int | None = Field(default=None, ge=1, le=10)
    recovery_threshold: int | None = Field(default=None, ge=1, le=10)

    @field_validator("headers")
    @classmethod
    def _headers(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return None if v is None else _validate_headers(v)


class StateOut(BaseModel):
    health: str
    next_run_at: datetime
    last_run_at: datetime | None
    last_failure_class: str | None = None
    open_incident_id: uuid.UUID | None = None


class SummaryOut(BaseModel):
    uptime_pct: float | None
    p95_ms: float | None
    sample_count: int


class MonitorOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    method: str
    interval_seconds: int
    timeout_ms: int
    headers: dict[str, str]
    assertions: list[dict[str, Any]]
    degraded_ms: int | None
    failure_threshold: int
    recovery_threshold: int
    is_paused: bool
    created_at: datetime
    state: StateOut | None = None
    summary_24h: SummaryOut | None = None


# --- runs / probe ------------------------------------------------------------------------


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime
    outcome: str
    failure_class: str | None
    http_status: int | None
    dns_ms: int | None
    connect_ms: int | None
    tls_ms: int | None
    ttfb_ms: int | None
    total_ms: int | None
    resolved_ip: IPvAnyAddress | None
    attempt_count: int
    error_detail: str | None
    trigger: str


class RunPage(BaseModel):
    items: list[RunSummary]
    next_cursor: str | None


class Timings(BaseModel):
    dns_ms: int | None
    connect_ms: int | None
    tls_ms: int | None
    ttfb_ms: int | None
    total_ms: int | None


class RunDetail(BaseModel):
    id: int
    monitor_id: uuid.UUID
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime
    outcome: str
    failure_class: str | None
    explanation: str
    timings: Timings
    resolved_ip: str | None
    attempt_count: int
    attempts: list[dict[str, Any]]
    http_status: int | None
    assertion_results: list[dict[str, Any]]
    response_headers: dict[str, str] | None
    response_snippet: str | None
    body_truncated: bool
    error_detail: str | None
    trigger: str
    worker_id: str


# --- stats / incidents -----------------------------------------------------------------


class StatsOut(BaseModel):
    window: str
    uptime_pct: float | None
    sample_count: int
    p50_ms: float | None
    p95_ms: float | None
    by_outcome: dict[str, int]
    by_failure_class: dict[str, int]


class IncidentEventOut(BaseModel):
    at: datetime
    kind: str
    run_id: int | None
    detail: dict[str, Any]


class IncidentOut(BaseModel):
    id: uuid.UUID
    monitor_id: uuid.UUID
    status: str
    opened_at: datetime
    resolved_at: datetime | None
    failure_count: int
    dominant_failure_class: str


class IncidentPage(BaseModel):
    items: list[IncidentOut]
    next_cursor: str | None


class IncidentDetail(IncidentOut):
    first_failure_run_id: int
    resolving_run_id: int | None
    events: list[IncidentEventOut]
    evidence: dict[str, Any]
