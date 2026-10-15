import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TZ = TIMESTAMP(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class Monitor(Base):
    """User-owned configuration. Runtime state lives in MonitorState."""

    __tablename__ = "monitors"
    __table_args__ = (
        UniqueConstraint("owner_id", "name"),
        CheckConstraint("method IN ('GET','HEAD')", name="method"),
        CheckConstraint("interval_seconds BETWEEN 30 AND 3600", name="interval"),
        CheckConstraint("timeout_ms BETWEEN 1000 AND 30000", name="timeout"),
        Index("ix_monitors_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    method: Mapped[str] = mapped_column(Text, server_default="GET")
    interval_seconds: Mapped[int] = mapped_column(server_default="60")
    timeout_ms: Mapped[int] = mapped_column(server_default="5000")
    headers: Mapped[dict[str, str]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    assertions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    degraded_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class MonitorState(Base):
    """Worker-owned runtime state, 1:1 with a monitor. Minimal in M1; grows in M4/M5."""

    __tablename__ = "monitor_state"
    __table_args__ = (
        CheckConstraint("health IN ('UNKNOWN','UP','DEGRADED','DOWN','PAUSED')", name="health"),
    )

    monitor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("monitors.id", ondelete="CASCADE"), primary_key=True
    )
    next_run_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())
    health: Mapped[str] = mapped_column(Text, server_default="UNKNOWN")
    last_run_id: Mapped[int | None] = mapped_column(BigInteger)
    last_run_at: Mapped[datetime | None] = mapped_column(TZ)


class CheckRun(Base):
    __tablename__ = "check_runs"
    __table_args__ = (
        # Idempotency key: one run per monitor per scheduled slot.
        UniqueConstraint("monitor_id", "scheduled_for"),
        CheckConstraint("outcome IN ('PASS','DEGRADED','FAIL','INCONCLUSIVE')", name="outcome"),
        CheckConstraint(
            "(failure_class IS NULL) = (outcome IN ('PASS','DEGRADED'))", name="failure_class"
        ),
        Index("ix_check_runs_monitor_started", "monitor_id", text("started_at DESC")),
        Index("ix_check_runs_started", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"))
    scheduled_for: Mapped[datetime] = mapped_column(TZ)
    started_at: Mapped[datetime] = mapped_column(TZ)
    finished_at: Mapped[datetime] = mapped_column(TZ)
    outcome: Mapped[str] = mapped_column(Text)
    failure_class: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    dns_ms: Mapped[int | None]
    connect_ms: Mapped[int | None]
    tls_ms: Mapped[int | None]
    ttfb_ms: Mapped[int | None]
    total_ms: Mapped[int | None]
    resolved_ip: Mapped[str | None] = mapped_column(INET)
    attempt_count: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    assertion_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    response_headers: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    response_snippet: Mapped[str | None] = mapped_column(Text)
    body_truncated: Mapped[bool] = mapped_column(server_default=text("false"))
    error_detail: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text, server_default="schedule")
