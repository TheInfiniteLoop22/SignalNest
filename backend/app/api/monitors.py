import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession, SettingsDep, get_policy, get_resolver
from app.api.schemas import MonitorCreate, MonitorOut, MonitorPatch, RunOut
from app.config import Settings
from app.db.models import CheckRun, Monitor, User
from app.probe.ssrf import SsrfPolicy, TargetError
from app.probe.types import DnsNameNotFound, DnsTimeout, Resolver

router = APIRouter(prefix="/monitors", tags=["monitors"])

Policy = Annotated[SsrfPolicy, Depends(get_policy)]
ResolverDep = Annotated[Resolver, Depends(get_resolver)]


async def check_target(url: str, policy: SsrfPolicy, resolver: Resolver) -> None:
    """Create/edit-time SSRF pre-check, so a bad URL is rejected early with a clear message.

    The worker re-checks on every probe (DNS can change), so this is a convenience, not the
    defence. A hostname that does not resolve *yet* is allowed: the service may not exist yet.
    """
    try:
        target = policy.validate_url(url)
    except TargetError as exc:
        raise HTTPException(422, str(exc)) from exc

    if target.is_ip_literal:
        ips = [target.host]
    else:
        try:
            ips = await resolver.resolve(target.host, 3.0)
        except (DnsNameNotFound, DnsTimeout):
            ips = []
    if any(not policy.allows_ip(ip) for ip in ips):
        raise HTTPException(422, "the target resolves to a non-public address")


def _check_interval(value: int, settings: Settings) -> None:
    if value < settings.min_interval_s:
        raise HTTPException(422, f"interval_seconds must be at least {settings.min_interval_s}")


async def owned_monitor(session: AsyncSession, user: User, monitor_id: uuid.UUID) -> Monitor:
    """Fetch a monitor the user owns, else 404 (never 403: don't confirm it exists)."""
    monitor = await session.scalar(
        select(Monitor).where(Monitor.id == monitor_id, Monitor.owner_id == user.id)
    )
    if monitor is None:
        raise HTTPException(404, "monitor not found")
    return monitor


async def _with_uptime(session: AsyncSession, monitors: list[Monitor]) -> list[MonitorOut]:
    """Attach 24h uptime (passes / probes) to each monitor with one grouped query."""
    passed = func.count().filter(CheckRun.outcome == "PASS")
    rows = await session.execute(
        select(CheckRun.monitor_id, passed, func.count())
        .where(
            CheckRun.monitor_id.in_([m.id for m in monitors]),
            CheckRun.started_at >= datetime.now(UTC) - timedelta(hours=24),
        )
        .group_by(CheckRun.monitor_id)
    )
    uptime = {mid: round(100 * ok / total, 2) for mid, ok, total in rows}
    out = []
    for m in monitors:
        item = MonitorOut.model_validate(m)
        item.uptime_24h_pct = uptime.get(m.id)
        out.append(item)
    return out


@router.get("", response_model=list[MonitorOut])
async def list_monitors(session: DbSession, user: CurrentUser) -> list[MonitorOut]:
    monitors = (
        await session.scalars(
            select(Monitor).where(Monitor.owner_id == user.id).order_by(Monitor.created_at)
        )
    ).all()
    return await _with_uptime(session, list(monitors))


@router.post("", status_code=201, response_model=MonitorOut)
async def create_monitor(
    body: MonitorCreate,
    session: DbSession,
    user: CurrentUser,
    settings: SettingsDep,
    policy: Policy,
    resolver: ResolverDep,
) -> MonitorOut:
    _check_interval(body.interval_seconds, settings)
    await check_target(body.url, policy, resolver)
    if body.webhook_url:
        await check_target(body.webhook_url, policy, resolver)

    # Lock the user's row so two simultaneous creates cannot both pass the cap check.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())
    count = await session.scalar(
        select(func.count()).select_from(Monitor).where(Monitor.owner_id == user.id)
    )
    if (count or 0) >= settings.max_monitors_per_user:
        raise HTTPException(422, f"monitor limit reached ({settings.max_monitors_per_user})")

    monitor = Monitor(owner_id=user.id, **body.model_dump())
    session.add(monitor)
    await _flush_or_conflict(session)
    await session.commit()
    return (await _with_uptime(session, [monitor]))[0]


@router.get("/{monitor_id}", response_model=MonitorOut)
async def get_monitor(monitor_id: uuid.UUID, session: DbSession, user: CurrentUser) -> MonitorOut:
    monitor = await owned_monitor(session, user, monitor_id)
    return (await _with_uptime(session, [monitor]))[0]


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
    # expected_status / body_contains may be cleared with an explicit null; the rest may not.
    for key in ("name", "url", "interval_seconds", "timeout_ms", "failure_threshold"):
        if key in changes and changes[key] is None:
            raise HTTPException(422, f"{key} cannot be null")

    if "url" in changes:
        await check_target(changes["url"], policy, resolver)
    if changes.get("webhook_url"):
        await check_target(changes["webhook_url"], policy, resolver)
    if "interval_seconds" in changes:
        _check_interval(changes["interval_seconds"], settings)
        # Pull the next run in if the new interval is shorter than the time left.
        soonest = datetime.now(UTC) + timedelta(seconds=changes["interval_seconds"])
        monitor.next_run_at = min(monitor.next_run_at, soonest)

    for key, value in changes.items():
        setattr(monitor, key, value)
    await _flush_or_conflict(session)
    await session.commit()
    return (await _with_uptime(session, [monitor]))[0]


@router.delete("/{monitor_id}", status_code=204)
async def delete_monitor(monitor_id: uuid.UUID, session: DbSession, user: CurrentUser) -> None:
    monitor = await owned_monitor(session, user, monitor_id)
    await session.delete(monitor)  # runs and incidents go with it (ON DELETE CASCADE)
    await session.commit()


@router.get("/{monitor_id}/runs", response_model=list[RunOut])
async def list_runs(
    monitor_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CheckRun]:
    await owned_monitor(session, user, monitor_id)
    runs = await session.scalars(
        select(CheckRun)
        .where(CheckRun.monitor_id == monitor_id)
        .order_by(CheckRun.started_at.desc(), CheckRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(runs)


async def _flush_or_conflict(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if "uq_monitors_owner_id" in str(exc.orig):
            raise HTTPException(409, "a monitor with that name already exists") from exc
        raise
