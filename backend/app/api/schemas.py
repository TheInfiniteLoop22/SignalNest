"""Request/response models. The database CHECK constraints back these up."""

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, field_validator

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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


class MonitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)
    interval_seconds: int = Field(default=60, ge=1, le=3600)
    timeout_ms: int = Field(default=5000, ge=1000, le=30000)
    expected_status: int | None = Field(default=None, ge=100, le=599)
    body_contains: str | None = Field(default=None, min_length=1, max_length=200)
    failure_threshold: int = Field(default=3, ge=1, le=10)
    webhook_url: str | None = Field(default=None, min_length=1, max_length=2048)


class MonitorPatch(BaseModel):
    """Every field optional; unspecified fields are left unchanged."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    interval_seconds: int | None = Field(default=None, ge=1, le=3600)
    timeout_ms: int | None = Field(default=None, ge=1000, le=30000)
    expected_status: int | None = Field(default=None, ge=100, le=599)
    body_contains: str | None = Field(default=None, min_length=1, max_length=200)
    failure_threshold: int | None = Field(default=None, ge=1, le=10)
    webhook_url: str | None = Field(default=None, min_length=1, max_length=2048)


class MonitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    interval_seconds: int
    timeout_ms: int
    expected_status: int | None
    body_contains: str | None
    failure_threshold: int
    webhook_url: str | None
    status: Literal["UNKNOWN", "UP", "DOWN"]
    last_checked_at: datetime | None
    created_at: datetime
    uptime_24h_pct: float | None = None


# --- runs / incidents --------------------------------------------------------------------


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    outcome: str
    failure_class: str | None
    http_status: int | None
    resolved_ip: IPvAnyAddress | None
    dns_ms: int | None
    connect_ms: int | None
    tls_ms: int | None
    ttfb_ms: int | None
    total_ms: int
    detail: str | None


class IncidentOut(BaseModel):
    id: uuid.UUID
    monitor_id: uuid.UUID
    monitor_name: str
    status: Literal["OPEN", "RESOLVED"]
    opened_at: datetime
    resolved_at: datetime | None
    failure_class: str
