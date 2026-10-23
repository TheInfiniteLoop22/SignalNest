import uuid
from datetime import datetime

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
from sqlalchemy.dialects.postgresql import BYTEA, INET, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TZ = TIMESTAMP(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[bytes] = mapped_column(BYTEA, unique=True)
    expires_at: Mapped[datetime] = mapped_column(TZ)


class Monitor(Base):
    """What to probe (user-owned config) plus the scheduler's bookkeeping for that monitor."""

    __tablename__ = "monitors"
    __table_args__ = (
        UniqueConstraint("owner_id", "name"),
        CheckConstraint("interval_seconds BETWEEN 1 AND 3600", name="interval"),
        CheckConstraint("timeout_ms BETWEEN 1000 AND 30000", name="timeout"),
        CheckConstraint("failure_threshold BETWEEN 1 AND 10", name="failure_threshold"),
        CheckConstraint("status IN ('UNKNOWN','UP','DOWN')", name="status"),
        Index("ix_monitors_owner_created", "owner_id", "created_at"),
        Index("ix_monitors_next_run_at", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())

    # configuration
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    interval_seconds: Mapped[int] = mapped_column(server_default="60")
    timeout_ms: Mapped[int] = mapped_column(server_default="5000")
    expected_status: Mapped[int | None]
    body_contains: Mapped[str | None] = mapped_column(Text)
    failure_threshold: Mapped[int] = mapped_column(SmallInteger, server_default="3")
    webhook_url: Mapped[str | None] = mapped_column(Text)

    # scheduler: a monitor is due when next_run_at has passed
    next_run_at: Mapped[datetime] = mapped_column(TZ, server_default=func.now())

    # health: DOWN means an incident is open
    status: Mapped[str] = mapped_column(Text, server_default="UNKNOWN")
    consecutive_failures: Mapped[int] = mapped_column(server_default="0")
    last_checked_at: Mapped[datetime | None] = mapped_column(TZ)


class CheckRun(Base):
    __tablename__ = "check_runs"
    __table_args__ = (
        CheckConstraint("outcome IN ('PASS','FAIL')", name="outcome"),
        CheckConstraint("(failure_class IS NULL) = (outcome = 'PASS')", name="failure_class"),
        Index("ix_check_runs_monitor_started", "monitor_id", text("started_at DESC")),
        Index("ix_check_runs_started_at", "started_at"),  # serves the retention delete
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(TZ)
    outcome: Mapped[str] = mapped_column(Text)
    failure_class: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    resolved_ip: Mapped[str | None] = mapped_column(INET)
    dns_ms: Mapped[int | None]
    connect_ms: Mapped[int | None]
    tls_ms: Mapped[int | None]
    ttfb_ms: Mapped[int | None]
    total_ms: Mapped[int]
    detail: Mapped[str | None] = mapped_column(Text)


class Incident(Base):
    """Open while resolved_at is NULL. At most one open incident per monitor."""

    __tablename__ = "incidents"
    __table_args__ = (
        Index(
            "one_open_incident",
            "monitor_id",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_incidents_monitor_opened", "monitor_id", "opened_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    monitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"))
    opened_at: Mapped[datetime] = mapped_column(TZ)
    resolved_at: Mapped[datetime | None] = mapped_column(TZ)
    failure_class: Mapped[str] = mapped_column(Text)
