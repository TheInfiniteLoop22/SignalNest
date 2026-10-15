"""M1 placeholder probe: a plain GET, total time only. Replaced by app/probe/engine.py in M2."""

import time
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CheckRun, Monitor, MonitorState


async def probe_monitor(
    session: AsyncSession,
    monitor_id: uuid.UUID,
    *,
    client: httpx.AsyncClient,
    worker_id: str,
) -> CheckRun:
    monitor = await session.get(Monitor, monitor_id)
    if monitor is None:
        raise LookupError(f"monitor {monitor_id} not found")

    started_at = datetime.now(UTC)
    t0 = time.monotonic()
    status: int | None = None
    failure_class: str | None = None
    detail: str | None = None
    try:
        resp = await client.request(
            monitor.method,
            monitor.url,
            timeout=monitor.timeout_ms / 1000,
            follow_redirects=False,
        )
        status = resp.status_code
        if not 200 <= status < 400:
            failure_class = "HTTP_UNEXPECTED_STATUS"
    except httpx.HTTPError as exc:
        # Real classification arrives in M2 (classify.py).
        failure_class = "UNCLASSIFIED_ERROR"
        detail = f"{type(exc).__name__}: {exc}"[:500]
    total_ms = round((time.monotonic() - t0) * 1000)

    run = CheckRun(
        monitor_id=monitor_id,
        scheduled_for=started_at,  # no scheduler yet (M4): slot == start time
        started_at=started_at,
        finished_at=datetime.now(UTC),
        outcome="PASS" if failure_class is None else "FAIL",
        failure_class=failure_class,
        http_status=status,
        total_ms=total_ms,
        error_detail=detail,
        worker_id=worker_id,
        trigger="manual",
    )
    session.add(run)
    await session.flush()

    state = await session.get(MonitorState, monitor_id)
    if state is not None:
        state.last_run_id = run.id
        state.last_run_at = run.finished_at
        state.health = "UP" if run.outcome == "PASS" else "DOWN"
    await session.commit()
    return run


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
