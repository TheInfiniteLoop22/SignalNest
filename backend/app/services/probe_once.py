"""Run one probe for a stored monitor and persist the evidence.

The probe itself is app.probe.engine (no DB). This module is the thin transaction wrapper;
the scheduler (M4) will split it into claim / execute / complete.
"""

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CheckRun, Monitor, MonitorState
from app.probe.engine import run_probe
from app.probe.types import (
    Outcome,
    ProbeRequest,
    ProbeResult,
    Resolver,
    TargetPolicy,
    assertion_list_adapter,
)

_HEALTH = {
    Outcome.PASS: "UP",
    Outcome.DEGRADED: "DEGRADED",
    Outcome.FAIL: "DOWN",
}


def _attempt_json(a: Any) -> dict[str, Any]:
    d = dataclasses.asdict(a)
    d["started_at"] = a.started_at.isoformat()
    d["error_class"] = a.error_class.value if a.error_class else None
    return d


async def probe_monitor(
    session: AsyncSession,
    monitor_id: uuid.UUID,
    *,
    worker_id: str,
    resolver: Resolver | None = None,
    policy: TargetPolicy | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[CheckRun, ProbeResult]:
    monitor = await session.get(Monitor, monitor_id)
    if monitor is None:
        raise LookupError(f"monitor {monitor_id} not found")

    request = ProbeRequest(
        url=monitor.url,
        method="HEAD" if monitor.method == "HEAD" else "GET",
        timeout_ms=monitor.timeout_ms,
        headers=monitor.headers,
        assertions=assertion_list_adapter.validate_python(monitor.assertions),
        degraded_ms=monitor.degraded_ms,
    )
    kwargs: dict[str, Any] = {"resolver": resolver, "transport": transport}
    if policy is not None:
        kwargs["policy"] = policy

    started_at = datetime.now(UTC)
    result = await run_probe(request, **kwargs)
    final = result.final

    run = CheckRun(
        monitor_id=monitor_id,
        scheduled_for=started_at,  # no scheduler yet (M4): slot == start time
        started_at=started_at,
        finished_at=datetime.now(UTC),
        outcome=result.outcome.value,
        failure_class=result.failure_class.value if result.failure_class else None,
        http_status=final.http_status,
        dns_ms=final.dns_ms,
        connect_ms=final.connect_ms,
        tls_ms=final.tls_ms,
        ttfb_ms=final.ttfb_ms,
        total_ms=final.total_ms,
        resolved_ip=final.resolved_ip,
        attempt_count=len(result.attempts),
        attempts=[_attempt_json(a) for a in result.attempts],
        assertion_results=[dataclasses.asdict(r) for r in result.assertion_results],
        response_headers=result.response_headers,
        response_snippet=result.response_snippet,
        body_truncated=result.body_truncated,
        error_detail=result.error_detail,
        worker_id=worker_id,
        trigger="manual",
    )
    session.add(run)
    await session.flush()

    state = await session.get(MonitorState, monitor_id)
    if state is not None:
        state.last_run_id = run.id
        state.last_run_at = run.finished_at
        if result.outcome is not Outcome.INCONCLUSIVE:
            state.health = _HEALTH[result.outcome]
    await session.commit()
    return run, result


async def list_runs(
    session: AsyncSession, monitor_id: uuid.UUID, *, limit: int = 50
) -> list[CheckRun]:
    stmt = (
        select(CheckRun)
        .where(CheckRun.monitor_id == monitor_id)
        .order_by(CheckRun.started_at.desc(), CheckRun.id.desc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())
