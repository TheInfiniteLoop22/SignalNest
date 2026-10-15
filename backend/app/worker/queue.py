"""Postgres as the job queue: claim due monitors, then record each result.

Two short transactions with the network probe between them. No transaction is ever held open
across network I/O.

TX1 (`claim_due`): `FOR UPDATE SKIP LOCKED` lets several workers poll the same table without
blocking each other or claiming the same row. The claim sets `locked_until`, a lease: if the
worker dies mid-probe, the lease expires and another worker picks the monitor up.

TX2 (`record_result`): store the run, advance the failure counter, open or resolve the
incident, and schedule the next run, all atomically.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CheckRun
from app.incidents.state_machine import Action, evaluate
from app.probe.types import ProbeResult

# Lease = 2 x timeout + 15 s: room for one probe plus TX2.
CLAIM_SQL = text(
    """
    WITH due AS (
        SELECT id FROM monitors
        WHERE next_run_at <= now() AND (locked_until IS NULL OR locked_until < now())
        ORDER BY next_run_at
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    )
    UPDATE monitors m
    SET locked_until = now() + make_interval(secs => 2 * m.timeout_ms / 1000.0 + 15)
    FROM due
    WHERE m.id = due.id
    RETURNING m.id, m.url, m.timeout_ms, m.expected_status, m.body_contains
    """
)


@dataclass(frozen=True)
class Claim:
    id: uuid.UUID
    url: str
    timeout_ms: int
    expected_status: int | None
    body_contains: str | None


async def claim_due(session: AsyncSession, *, limit: int) -> list[Claim]:
    """Claim up to `limit` due monitors in one short transaction and commit."""
    if limit <= 0:
        return []
    result = await session.execute(CLAIM_SQL, {"limit": limit})
    claims = [Claim(**row._mapping) for row in result]
    await session.commit()
    return claims


async def record_result(
    session: AsyncSession, claim: Claim, result: ProbeResult, *, started_at: datetime
) -> bool:
    """Persist one probe result. Returns False if the monitor was deleted while probing."""
    # Locking the monitor row serialises completions and makes a delete wait for us.
    row = (
        (
            await session.execute(
                text(
                    """
                    SELECT status, consecutive_failures, failure_threshold, interval_seconds
                    FROM monitors WHERE id = :id FOR UPDATE
                    """
                ),
                {"id": claim.id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        await session.rollback()
        return False

    session.add(
        CheckRun(
            monitor_id=claim.id,
            started_at=started_at,
            outcome=result.outcome.value,
            failure_class=result.failure_class.value if result.failure_class else None,
            http_status=result.http_status,
            resolved_ip=result.resolved_ip,
            dns_ms=result.dns_ms,
            connect_ms=result.connect_ms,
            tls_ms=result.tls_ms,
            ttfb_ms=result.ttfb_ms,
            total_ms=result.total_ms,
            detail=result.detail,
        )
    )
    await session.flush()

    decision = evaluate(
        status=row["status"],
        consecutive_failures=row["consecutive_failures"],
        passed=result.failure_class is None,
        failure_threshold=row["failure_threshold"],
    )

    if decision.action is Action.OPEN:
        # Backdate to the first failure of the streak, which is when the outage began.
        await session.execute(
            text(
                """
                INSERT INTO incidents (monitor_id, opened_at, failure_class)
                VALUES (
                    :m,
                    (SELECT min(started_at) FROM (
                        SELECT started_at FROM check_runs
                        WHERE monitor_id = :m ORDER BY started_at DESC LIMIT :n) streak),
                    :fc
                )
                """
            ),
            {
                "m": claim.id,
                "n": decision.consecutive_failures,
                "fc": result.failure_class.value if result.failure_class else "ERROR",
            },
        )
    elif decision.action is Action.RESOLVE:
        await session.execute(
            text(
                "UPDATE incidents SET resolved_at = now() "
                "WHERE monitor_id = :m AND resolved_at IS NULL"
            ),
            {"m": claim.id},
        )

    await session.execute(
        text(
            """
            UPDATE monitors
            SET status = :status,
                consecutive_failures = :failures,
                last_checked_at = now(),
                next_run_at = now() + make_interval(secs => :interval),
                locked_until = NULL
            WHERE id = :id
            """
        ),
        {
            "status": decision.status,
            "failures": decision.consecutive_failures,
            "interval": row["interval_seconds"],
            "id": claim.id,
        },
    )
    await session.commit()
    return True
