"""Deliver queued webhook alerts, with retries.

Same shape as the probe queue: claim rows with `FOR UPDATE SKIP LOCKED` and a lease in one short
transaction, make the HTTP call with no transaction open, then record the outcome in a second.
The POST goes through the probe engine, so webhook URLs get the same SSRF protection as
monitored URLs. A failed attempt is retried with exponential backoff until `max_attempts`.
"""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import ProbeRequest, Resolver

LEASE_S = 60
TIMEOUT_MS = 5000
BACKOFF_BASE_S = 30
BACKOFF_CAP_S = 3600

CLAIM_ALERTS_SQL = text(
    """
    WITH due AS (
        SELECT a.id FROM alerts a
        WHERE a.status = 'PENDING' AND a.next_attempt_at <= now()
          AND (a.locked_until IS NULL OR a.locked_until < now())
        ORDER BY a.next_attempt_at
        LIMIT :limit
        FOR UPDATE OF a SKIP LOCKED
    )
    UPDATE alerts a
    SET locked_until = now() + make_interval(secs => :lease)
    FROM due, monitors m
    WHERE a.id = due.id AND m.id = a.monitor_id
    RETURNING a.id, a.attempts, a.payload, m.webhook_url
    """
)


@dataclass(frozen=True)
class PendingAlert:
    id: int
    attempts: int
    payload: dict[str, Any]
    webhook_url: str | None


def backoff_s(attempts: int) -> int:
    """Seconds to wait after the `attempts`-th failure: 30, 60, 120, ... capped at an hour."""
    return int(min(BACKOFF_BASE_S * 2 ** max(attempts - 1, 0), BACKOFF_CAP_S))


async def claim_alerts(session: AsyncSession, *, limit: int) -> list[PendingAlert]:
    result = await session.execute(CLAIM_ALERTS_SQL, {"limit": limit, "lease": LEASE_S})
    alerts = [PendingAlert(**row._mapping) for row in result]
    await session.commit()
    return alerts


async def deliver(
    alert: PendingAlert,
    *,
    policy: SsrfPolicy,
    resolver: Resolver | None = None,
    transport: Any = None,
) -> str | None:
    """POST the payload. Returns None on success, else a short error description."""
    if not alert.webhook_url:
        return "monitor has no webhook configured"
    result = await run_probe(
        ProbeRequest(
            url=alert.webhook_url,
            timeout_ms=TIMEOUT_MS,
            method="POST",
            headers=(("Content-Type", "application/json"),),
            body=json.dumps(alert.payload).encode(),
        ),
        policy=policy,
        resolver=resolver,
        transport=transport,
    )
    if result.failure_class is None:
        return None
    return f"{result.failure_class.value}: {result.detail or ''}"[:300]


async def record_delivery(
    session: AsyncSession, alert: PendingAlert, error: str | None, *, max_attempts: int
) -> None:
    attempts = alert.attempts + 1
    if error is None:
        status, wait = "DELIVERED", 0
    elif attempts >= max_attempts or not alert.webhook_url:
        status, wait = "FAILED", 0
    else:
        status, wait = "PENDING", backoff_s(attempts)
    await session.execute(
        text(
            """
            UPDATE alerts
            SET status = :status, attempts = :attempts, last_error = :error, locked_until = NULL,
                next_attempt_at = now() + make_interval(secs => :wait)
            WHERE id = :id
            """
        ),
        {"status": status, "attempts": attempts, "error": error, "wait": wait, "id": alert.id},
    )
    await session.commit()
