"""Run one probe for a stored monitor (CLI / dev path) and persist the evidence.

The scheduled path lives in app/worker (claim -> execute -> complete). This helper is the
manual one: no lease, trigger="manual".
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import CheckRun, Monitor, MonitorState
from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import (
    Outcome,
    ProbeRequest,
    ProbeResult,
    Resolver,
    assertion_list_adapter,
)
from app.services.run_record import HEALTH_BY_OUTCOME, build_run_values


def policy_from_settings() -> SsrfPolicy:
    cfg = get_settings()
    return SsrfPolicy.from_settings(
        cfg.allowed_ports, cfg.ssrf_allow_private_cidrs, cfg.allowed_hosts
    )


def request_for(monitor: Monitor) -> ProbeRequest:
    return ProbeRequest(
        url=monitor.url,
        method="HEAD" if monitor.method == "HEAD" else "GET",
        timeout_ms=monitor.timeout_ms,
        headers=monitor.headers,
        assertions=assertion_list_adapter.validate_python(monitor.assertions),
        degraded_ms=monitor.degraded_ms,
    )


async def probe_monitor(
    session: AsyncSession,
    monitor_id: uuid.UUID,
    *,
    worker_id: str,
    resolver: Resolver | None = None,
    policy: SsrfPolicy | None = None,
    transport: Any = None,
) -> tuple[CheckRun, ProbeResult]:
    monitor = await session.get(Monitor, monitor_id)
    if monitor is None:
        raise LookupError(f"monitor {monitor_id} not found")

    started_at = datetime.now(UTC)
    result = await run_probe(
        request_for(monitor),
        resolver=resolver,
        transport=transport,
        policy=policy or policy_from_settings(),
    )

    # A manual run's slot is its start time, nudged past the latest existing slot because
    # UNIQUE(monitor_id, scheduled_for) and coarse clocks can collide.
    latest = await session.scalar(
        select(func.max(CheckRun.scheduled_for)).where(CheckRun.monitor_id == monitor_id)
    )
    slot = started_at
    if latest is not None and latest >= started_at:
        slot = latest + timedelta(microseconds=1)

    run = CheckRun(
        **build_run_values(
            monitor_id=monitor_id,
            scheduled_for=slot,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            result=result,
            worker_id=worker_id,
            trigger="manual",
        )
    )
    session.add(run)
    await session.flush()

    state = await session.get(MonitorState, monitor_id)
    if state is not None:
        state.last_run_id = run.id
        state.last_run_at = run.finished_at
        if result.outcome is not Outcome.INCONCLUSIVE:
            state.health = HEALTH_BY_OUTCOME[result.outcome]
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
