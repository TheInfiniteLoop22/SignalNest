import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    DbSession,
    SettingsDep,
    enforce,
    get_policy,
    get_resolver,
    limiter,
    require_csrf_header,
)
from app.api.errors import Problem
from app.api.ratelimit import FixedWindowLimiter
from app.api.schemas import (
    MonitorCreate,
    MonitorOut,
    MonitorPatch,
    RunPage,
    RunSummary,
    SeriesPoint,
    StateOut,
    StatsOut,
    SummaryOut,
)
from app.config import Settings
from app.db.models import Monitor, MonitorState, User
from app.probe.ssrf import BlockedTarget, SsrfPolicy, TargetError
from app.probe.types import DnsNameNotFound, DnsTimeout, Resolver
from app.services.cursor import BadCursor, decode_cursor, encode_cursor

router = APIRouter(
    prefix="/monitors", tags=["monitors"], dependencies=[Depends(require_csrf_header)]
)

Policy = Annotated[SsrfPolicy, Depends(get_policy)]
ResolverDep = Annotated[Resolver, Depends(get_resolver)]
CheckLimiter = Annotated[FixedWindowLimiter, Depends(limiter("check_now"))]


# --- helpers --------------------------------------------------------------------------------


async def check_target(url: str, policy: SsrfPolicy, resolver: Resolver) -> str:
    """Create/edit-time SSRF pre-check. Execution re-checks every attempt (DNS can change).

    Returns the normalised hostname. A hostname that does not resolve *yet* is allowed: the
    service may not be deployed. One that resolves to a denied address is rejected.
    """
    try:
        target = policy.validate_url(url)
    except TargetError as exc:
        raise Problem(422, "invalid-target", str(exc)) from exc
    except BlockedTarget as exc:
        raise Problem(422, "blocked-target", str(exc)) from exc

    if target.is_ip_literal:
        ips = [target.host]
    else:
        try:
            ips = await resolver.resolve(target.host, 3.0)
        except (DnsNameNotFound, DnsTimeout):
            ips = []
    if any(not policy.allows_ip(ip) for ip in ips):
        raise Problem(422, "blocked-target", "the target resolves to a non-public address")
    return target.host


def _min_interval(value: int, settings: Settings) -> None:
    if value < settings.min_interval_s:
        raise Problem(
            422,
            "validation",
            f"interval_seconds must be at least {settings.min_interval_s}",
            errors=[{"field": "interval_seconds", "message": "below the minimum interval"}],
        )


async def owned_monitor(session: AsyncSession, user: User, monitor_id: uuid.UUID) -> Monitor:
    """Fetch a monitor the user owns, else 404 (never 403: don't confirm it exists)."""
    monitor = await session.scalar(
        select(Monitor).where(Monitor.id == monitor_id, Monitor.owner_id == user.id)
    )
    if monitor is None:
        raise Problem(404, "not-found", "monitor not found")
    return monitor


def _monitor_out(
    m: Monitor,
    state: StateOut | None = None,
    summary: SummaryOut | None = None,
) -> MonitorOut:
    return MonitorOut(
        id=m.id,
        name=m.name,
        url=m.url,
        method=m.method,
        interval_seconds=m.interval_seconds,
        timeout_ms=m.timeout_ms,
        headers=m.headers,
        assertions=m.assertions,
        degraded_ms=m.degraded_ms,
        failure_threshold=m.failure_threshold,
        recovery_threshold=m.recovery_threshold,
        is_paused=m.is_paused,
        created_at=m.created_at,
        state=state,
        summary_24h=summary,
    )


async def _states(
    session: AsyncSession, ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, StateOut], dict[uuid.UUID, SummaryOut]]:
    if not ids:
        return {}, {}
    st = (
        (
            await session.execute(
                text(
                    """
                    SELECT s.monitor_id, s.health, s.next_run_at, s.last_run_at,
                           (SELECT r.failure_class FROM check_runs r
                             WHERE r.monitor_id = s.monitor_id AND r.outcome = 'FAIL'
                             ORDER BY r.started_at DESC LIMIT 1) AS last_failure_class,
                           (SELECT i.id FROM incidents i
                             WHERE i.monitor_id = s.monitor_id AND i.status = 'OPEN') AS incident
                    FROM monitor_state s WHERE s.monitor_id = ANY(:ids)
                    """
                ),
                {"ids": ids},
            )
        )
        .mappings()
        .all()
    )
    states = {
        r["monitor_id"]: StateOut(
            health=r["health"],
            next_run_at=r["next_run_at"],
            last_run_at=r["last_run_at"],
            last_failure_class=r["last_failure_class"],
            open_incident_id=r["incident"],
        )
        for r in st
    }
    agg = (
        (
            await session.execute(
                text(
                    """
                    SELECT monitor_id,
                           count(*) FILTER (WHERE outcome <> 'INCONCLUSIVE') AS n,
                           count(*) FILTER (WHERE outcome IN ('PASS','DEGRADED')) AS ok,
                           percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms)
                               FILTER (WHERE outcome IN ('PASS','DEGRADED')) AS p95
                    FROM check_runs
                    WHERE monitor_id = ANY(:ids) AND started_at >= now() - interval '24 hours'
                    GROUP BY monitor_id
                    """
                ),
                {"ids": ids},
            )
        )
        .mappings()
        .all()
    )
    summaries = {
        r["monitor_id"]: SummaryOut(
            uptime_pct=round(100 * r["ok"] / r["n"], 2) if r["n"] else None,
            p95_ms=float(r["p95"]) if r["p95"] is not None else None,
            sample_count=int(r["n"]),
        )
        for r in agg
    }
    return states, summaries


async def _out(session: AsyncSession, m: Monitor) -> MonitorOut:
    states, summaries = await _states(session, [m.id])
    return _monitor_out(
        m,
        states.get(m.id),
        summaries.get(m.id) or SummaryOut(uptime_pct=None, p95_ms=None, sample_count=0),
    )


def _is_duplicate_name(exc: IntegrityError) -> bool:
    return "uq_monitors_owner_id" in str(exc.orig)


def _assertions_json(items: list[Any]) -> list[dict[str, Any]]:
    return [a.model_dump(mode="json", by_alias=True, exclude_none=True) for a in items]


# --- CRUD -----------------------------------------------------------------------------------


@router.get("", response_model=list[MonitorOut])
async def list_monitors(
    session: DbSession,
    user: CurrentUser,
    health: Annotated[
        Literal["UNKNOWN", "UP", "DEGRADED", "DOWN", "PAUSED"] | None, Query()
    ] = None,
) -> list[MonitorOut]:
    monitors = (
        await session.scalars(
            select(Monitor).where(Monitor.owner_id == user.id).order_by(Monitor.created_at)
        )
    ).all()
    states, summaries = await _states(session, [m.id for m in monitors])
    out = [
        _monitor_out(
            m,
            states.get(m.id),
            summaries.get(m.id) or SummaryOut(uptime_pct=None, p95_ms=None, sample_count=0),
        )
        for m in monitors
    ]
    if health:
        out = [o for o in out if o.state and o.state.health == health]
    return out


@router.post("", status_code=201, response_model=MonitorOut)
async def create_monitor(
    body: MonitorCreate,
    session: DbSession,
    user: CurrentUser,
    settings: SettingsDep,
    policy: Policy,
    resolver: ResolverDep,
) -> MonitorOut:
    _min_interval(body.interval_seconds, settings)
    host = await check_target(body.url, policy, resolver)

    existing = (await session.scalars(select(Monitor.url).where(Monitor.owner_id == user.id))).all()
    if len(existing) >= settings.max_monitors_per_user:
        raise Problem(
            422,
            "monitor-limit",
            f"monitor limit reached ({settings.max_monitors_per_user})",
        )
    same_host = sum(1 for u in existing if (urlsplit(u).hostname or "").lower() == host)
    if same_host >= settings.max_monitors_per_host:
        raise Problem(
            422,
            "host-limit",
            f"at most {settings.max_monitors_per_host} monitors per hostname",
        )

    monitor = Monitor(
        owner_id=user.id,
        name=body.name,
        url=body.url,
        method=body.method,
        interval_seconds=body.interval_seconds,
        timeout_ms=body.timeout_ms,
        headers=body.headers,
        assertions=_assertions_json(body.assertions),
        degraded_ms=body.degraded_ms,
        failure_threshold=body.failure_threshold,
        recovery_threshold=body.recovery_threshold,
    )
    session.add(monitor)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if _is_duplicate_name(exc):
            raise Problem(409, "duplicate-name", "a monitor with that name already exists") from exc
        raise
    # Spread first runs over one interval so new monitors do not stampede.
    jitter = random.uniform(0, body.interval_seconds)
    session.add(
        MonitorState(
            monitor_id=monitor.id, next_run_at=datetime.now(UTC) + timedelta(seconds=jitter)
        )
    )
    await session.commit()
    return await _out(session, monitor)


@router.get("/{monitor_id}", response_model=MonitorOut)
async def get_monitor(monitor_id: uuid.UUID, session: DbSession, user: CurrentUser) -> MonitorOut:
    return await _out(session, await owned_monitor(session, user, monitor_id))


@router.patch("/{monitor_id}", response_model=MonitorOut)
async def patch_monitor(
    monitor_id: uuid.UUID,
    body: MonitorPatch,
    session: DbSession,
    user: CurrentUser,
    settings: SettingsDep,
    policy: Policy,
    resolver: ResolverDep,
) -> MonitorOut:
    monitor = await owned_monitor(session, user, monitor_id)
    changes = body.model_dump(exclude_unset=True)
    if "assertions" in changes and changes["assertions"] is not None:
        changes["assertions"] = _assertions_json(body.assertions or [])
    for key in ("name", "url", "method", "interval_seconds", "timeout_ms", "headers",
                "assertions", "failure_threshold", "recovery_threshold"):  # fmt: skip
        if key in changes and changes[key] is None:
            raise Problem(422, "validation", f"{key} cannot be null")

    if "url" in changes:
        await check_target(changes["url"], policy, resolver)
    if "interval_seconds" in changes:
        _min_interval(changes["interval_seconds"], settings)

    degraded = changes.get("degraded_ms", monitor.degraded_ms)
    timeout = changes.get("timeout_ms", monitor.timeout_ms)
    if degraded is not None and degraded >= timeout:
        raise Problem(422, "validation", "degraded_ms must be lower than timeout_ms")

    for key, value in changes.items():
        setattr(monitor, key, value)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if _is_duplicate_name(exc):
            raise Problem(409, "duplicate-name", "a monitor with that name already exists") from exc
        raise
    if "interval_seconds" in changes:
        await session.execute(
            text(
                """
                UPDATE monitor_state
                SET next_run_at = LEAST(next_run_at, now() + make_interval(secs => :s))
                WHERE monitor_id = :id
                """
            ),
            {"s": monitor.interval_seconds, "id": monitor.id},
        )
    await session.commit()
    return await _out(session, monitor)


@router.delete("/{monitor_id}", status_code=204)
async def delete_monitor(monitor_id: uuid.UUID, session: DbSession, user: CurrentUser) -> None:
    monitor = await owned_monitor(session, user, monitor_id)
    # incidents reference runs with ON DELETE RESTRICT, so remove them before the runs go.
    await session.execute(text("DELETE FROM incidents WHERE monitor_id = :id"), {"id": monitor.id})
    await session.delete(monitor)
    await session.commit()


# --- control --------------------------------------------------------------------------------


@router.post("/{monitor_id}/pause", status_code=204)
async def pause(monitor_id: uuid.UUID, session: DbSession, user: CurrentUser) -> None:
    monitor = await owned_monitor(session, user, monitor_id)
    monitor.is_paused = True
    # An open incident is intentionally left open (ADR-020).
    await session.execute(
        text("UPDATE monitor_state SET health = 'PAUSED' WHERE monitor_id = :id"),
        {"id": monitor.id},
    )
    await session.commit()


@router.post("/{monitor_id}/resume", status_code=204)
async def resume(monitor_id: uuid.UUID, session: DbSession, user: CurrentUser) -> None:
    monitor = await owned_monitor(session, user, monitor_id)
    if not monitor.is_paused:
        return
    monitor.is_paused = False
    await session.execute(
        text(
            """
            UPDATE monitor_state
            SET health = CASE WHEN incident_open THEN 'DOWN' ELSE 'UNKNOWN' END,
                consecutive_failures = 0, consecutive_successes = 0,
                streak_first_run_id = NULL, streak_first_at = NULL,
                streak_class_counts = '{}'::jsonb,
                next_run_at = now()
            WHERE monitor_id = :id
            """
        ),
        {"id": monitor.id},
    )
    await session.commit()


@router.post("/{monitor_id}/check", status_code=202)
async def check_now(
    monitor_id: uuid.UUID, session: DbSession, user: CurrentUser, rate: CheckLimiter
) -> dict[str, str]:
    monitor = await owned_monitor(session, user, monitor_id)
    if monitor.is_paused:
        raise Problem(409, "paused", "the monitor is paused")
    enforce(rate, str(monitor.id))
    # The worker stays the only scheduler: we just make the monitor due. If it is leased a run
    # is already in progress, so there is nothing to do.
    await session.execute(
        text(
            "UPDATE monitor_state SET next_run_at = now() "
            "WHERE monitor_id = :id AND lease_owner IS NULL"
        ),
        {"id": monitor.id},
    )
    await session.commit()
    return {"status": "scheduled"}


# --- history --------------------------------------------------------------------------------


@router.get("/{monitor_id}/runs", response_model=RunPage)
async def list_runs(
    monitor_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    outcome: Literal["PASS", "DEGRADED", "FAIL", "INCONCLUSIVE"] | None = None,
    failure_class: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
) -> RunPage:
    await owned_monitor(session, user, monitor_id)
    where = ["monitor_id = :m"]
    params: dict[str, Any] = {"m": monitor_id, "limit": limit + 1}
    if outcome:
        where.append("outcome = :outcome")
        params["outcome"] = outcome
    if failure_class:
        where.append("failure_class = :fc")
        params["fc"] = failure_class
    if from_:
        where.append("started_at >= :from_")
        params["from_"] = from_
    if to:
        where.append("started_at <= :to")
        params["to"] = to
    if cursor:
        try:
            ts, rid = decode_cursor(cursor)
        except BadCursor as exc:
            raise Problem(422, "validation", "invalid cursor") from exc
        # Keyset: strictly after the last row of the previous page in (started_at, id) order.
        where.append("(started_at, id) < (:c_ts, :c_id)")
        params["c_ts"], params["c_id"] = ts, rid

    rows = (
        (
            await session.execute(
                text(
                    f"""
                    SELECT id, scheduled_for, started_at, finished_at, outcome, failure_class,
                           http_status, dns_ms, connect_ms, tls_ms, ttfb_ms, total_ms,
                           resolved_ip, attempt_count, error_detail, trigger
                    FROM check_runs
                    WHERE {" AND ".join(where)}
                    ORDER BY started_at DESC, id DESC
                    LIMIT :limit
                    """  # noqa: S608 - only fixed fragments are interpolated; values are bound
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1]["started_at"], page[-1]["id"]) if len(rows) > limit else None
    )
    return RunPage(
        items=[RunSummary.model_validate(dict(r)) for r in page], next_cursor=next_cursor
    )


# --- stats ----------------------------------------------------------------------------------


@router.get("/{monitor_id}/stats", response_model=StatsOut)
async def stats(
    monitor_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    window: Annotated[Literal["24h", "7d"], Query()] = "24h",
) -> StatsOut:
    await owned_monitor(session, user, monitor_id)
    interval = timedelta(hours=24) if window == "24h" else timedelta(days=7)
    bucket = timedelta(hours=1) if window == "24h" else timedelta(hours=6)
    params = {"m": monitor_id, "interval": interval, "bucket": bucket}

    outcome_rows = (
        await session.execute(
            text(
                """
                SELECT outcome, count(*) FROM check_runs
                WHERE monitor_id = :m AND started_at >= now() - CAST(:interval AS interval)
                GROUP BY outcome
                """
            ),
            params,
        )
    ).all()
    by_outcome = {r[0]: int(r[1]) for r in outcome_rows}

    class_rows = (
        await session.execute(
            text(
                """
                SELECT failure_class, count(*) FROM check_runs
                WHERE monitor_id = :m AND started_at >= now() - CAST(:interval AS interval)
                  AND failure_class IS NOT NULL
                GROUP BY failure_class
                """
            ),
            params,
        )
    ).all()

    # Uptime = healthy / (healthy + failed). INCONCLUSIVE is excluded: our problems are not
    # the target's downtime. Latency percentiles use healthy runs only, so a 5 s timeout does
    # not masquerade as latency.
    totals = (
        (
            await session.execute(
                text(
                    """
                    SELECT count(*) FILTER (WHERE outcome <> 'INCONCLUSIVE') AS n,
                           count(*) FILTER (WHERE outcome IN ('PASS','DEGRADED')) AS ok,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms)
                               FILTER (WHERE outcome IN ('PASS','DEGRADED')) AS p50,
                           percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms)
                               FILTER (WHERE outcome IN ('PASS','DEGRADED')) AS p95
                    FROM check_runs
                    WHERE monitor_id = :m AND started_at >= now() - CAST(:interval AS interval)
                    """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    series_rows = (
        (
            await session.execute(
                text(
                    """
                    SELECT date_bin(CAST(:bucket AS interval), started_at,
                                    TIMESTAMPTZ '2000-01-01') AS bucket,
                           count(*) FILTER (WHERE outcome <> 'INCONCLUSIVE') AS n,
                           count(*) FILTER (WHERE outcome = 'FAIL') AS fails,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms)
                               FILTER (WHERE outcome IN ('PASS','DEGRADED')) AS p50,
                           percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms)
                               FILTER (WHERE outcome IN ('PASS','DEGRADED')) AS p95
                    FROM check_runs
                    WHERE monitor_id = :m AND started_at >= now() - CAST(:interval AS interval)
                    GROUP BY 1 ORDER BY 1
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    n = int(totals["n"])
    return StatsOut(
        window=window,
        uptime_pct=round(100 * int(totals["ok"]) / n, 2) if n else None,
        sample_count=n,
        p50_ms=float(totals["p50"]) if totals["p50"] is not None else None,
        p95_ms=float(totals["p95"]) if totals["p95"] is not None else None,
        by_outcome=by_outcome,
        by_failure_class={r[0]: int(r[1]) for r in class_rows},
        series=[
            SeriesPoint(
                bucket=r["bucket"],
                p50_ms=float(r["p50"]) if r["p50"] is not None else None,
                p95_ms=float(r["p95"]) if r["p95"] is not None else None,
                sample_count=int(r["n"]),
                fail_count=int(r["fails"]),
            )
            for r in series_rows
        ],
    )


__all__ = ["Response", "check_target", "router"]
