"""TX1: claim due monitors with a time-bounded lease (ADR-002, docs/05 section 4)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# `:now` is NULL in production (the database clock decides, so process clock skew cannot
# matter); tests pass an explicit instant.
#
# lease = 2 x timeout + 15 s: generous room for one probe plus TX2.
CLAIM_SQL = text(
    """
    WITH t AS (SELECT COALESCE(CAST(:now AS timestamptz), now()) AS now),
    due AS (
        SELECT s.monitor_id
        FROM monitor_state s
        JOIN monitors m ON m.id = s.monitor_id, t
        WHERE m.is_paused = false
          AND s.next_run_at <= t.now
          AND (s.lease_owner IS NULL OR s.lease_expires_at < t.now)
        ORDER BY s.next_run_at
        LIMIT :limit
        FOR UPDATE OF s SKIP LOCKED
    )
    UPDATE monitor_state s
    SET lease_owner = :worker_id,
        lease_expires_at = t.now + make_interval(secs => 2 * m.timeout_ms / 1000.0 + 15)
    FROM due, monitors m, t
    WHERE s.monitor_id = due.monitor_id
      AND m.id = s.monitor_id
    RETURNING s.monitor_id, s.next_run_at AS scheduled_for,
              m.url, m.method, m.timeout_ms, m.headers, m.assertions,
              m.degraded_ms, m.interval_seconds
    """
)


@dataclass(frozen=True)
class Claim:
    monitor_id: uuid.UUID
    scheduled_for: datetime
    url: str
    method: str
    timeout_ms: int
    headers: dict[str, str]
    assertions: list[dict[str, Any]]
    degraded_ms: int | None
    interval_seconds: int


async def claim_due(
    session: AsyncSession,
    *,
    worker_id: str,
    limit: int,
    now: datetime | None = None,
) -> list[Claim]:
    """Claim up to `limit` due monitors in one short transaction and commit."""
    if limit <= 0:
        return []
    result = await session.execute(CLAIM_SQL, {"worker_id": worker_id, "limit": limit, "now": now})
    claims = [Claim(**row._mapping) for row in result]
    await session.commit()
    return claims


async def release_leases(
    session: AsyncSession, *, worker_id: str, monitor_ids: list[uuid.UUID]
) -> int:
    """Give claimed-but-unfinished monitors back (graceful shutdown). The slot is untouched,
    so the next claim runs the same scheduled_for."""
    if not monitor_ids:
        return 0
    result = await session.execute(
        text(
            """
            UPDATE monitor_state
            SET lease_owner = NULL, lease_expires_at = NULL
            WHERE lease_owner = :worker_id AND monitor_id = ANY(:ids)
            """
        ),
        {"worker_id": worker_id, "ids": monitor_ids},
    )
    await session.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
