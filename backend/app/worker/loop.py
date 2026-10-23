"""The worker: one loop that starts a check for every monitor that is due.

    every second:
        1. find monitors whose next_run_at has passed (skipping ones already being checked)
        2. start each check as its own task: probe, then save the result (no database
           transaction is open while waiting on the network)

Each check is an independent task, so one slow or hanging site never delays the others. A set
of "in flight" monitor ids stops a monitor from being started twice while its check is still
running. If the process dies mid-check, nothing was written, the monitor is still due
afterwards, and it is simply checked again.
"""

import asyncio
import functools
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import CheckRun, Incident, Monitor, Session
from app.incidents.state_machine import Action, evaluate
from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import ProbeRequest, ProbeResult, Resolver
from app.worker.webhook import notify

log = logging.getLogger("signalnest.worker")

DB_ERRORS = (DBAPIError, InterfaceError, OSError)
CLEANUP_EVERY = timedelta(hours=1)


class Worker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        policy: SsrfPolicy,
        batch_size: int = 20,
        poll_interval_s: float = 1.0,
        retention_days: int = 30,
        shutdown_grace_s: float = 20.0,
        resolver: Resolver | None = None,
        transport: Any = None,
    ) -> None:
        self.sessions = sessions
        self.policy = policy
        self.batch_size = batch_size  # most checks running at the same time
        self.poll_interval_s = poll_interval_s
        self.retention_days = retention_days
        self.shutdown_grace_s = shutdown_grace_s
        self.resolver = resolver
        self.transport = transport
        self._stop = asyncio.Event()
        self._in_flight: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._slot_free = asyncio.Event()  # set whenever a running check finishes
        self._more_waiting = False  # the last look at the schedule filled every free slot
        self._last_cleanup = datetime.min.replace(tzinfo=UTC)

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    def request_stop(self) -> None:
        self._stop.set()
        self._slot_free.set()  # wake a loop that is waiting for a slot

    async def run(self) -> None:
        log.info("worker started")
        try:
            while not self._stop.is_set():
                self._more_waiting = False
                try:
                    await self.run_once()
                    await self._cleanup_if_due()
                except DB_ERRORS as exc:
                    log.error("database unavailable, retrying: %s", exc)
                except Exception:
                    # A bug must not end the loop: monitoring would silently stop.
                    log.exception("unexpected error in the worker loop; carrying on")
                if self._more_waiting:
                    await (
                        self._wait_for_free_slot()
                    )  # work is queued: continue as soon as a slot frees
                else:
                    await self._sleep(self.poll_interval_s)  # nothing due: look again in a second
        finally:
            await self.wait_idle(self.shutdown_grace_s, cancel_rest=True)
            log.info("worker stopped")

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _wait_for_free_slot(self) -> None:
        """Wait until a running check finishes, or the poll interval passes."""
        if len(self._in_flight) < self.batch_size:
            return
        self._slot_free.clear()
        try:
            await asyncio.wait_for(self._slot_free.wait(), timeout=self.poll_interval_s)
        except TimeoutError:
            pass

    async def run_once(self) -> int:
        """Start a check for every due monitor that is not already running. Returns how many."""
        free = self.batch_size - len(self._in_flight)
        if free <= 0:
            self._more_waiting = True  # every slot is busy, so anything due has to wait for one
            return 0
        query = (
            select(Monitor)
            .where(Monitor.next_run_at <= func.now())  # the database's clock
            .order_by(Monitor.next_run_at)
            .limit(free)
        )
        if self._in_flight:
            query = query.where(Monitor.id.not_in(list(self._in_flight)))
        async with self.sessions() as session:
            due = list(await session.scalars(query))
        self._more_waiting = len(due) == free  # a full page: there may be more behind it
        for monitor in due:
            task = asyncio.create_task(self._check(monitor), name=f"check-{monitor.id}")
            self._in_flight[monitor.id] = task
            task.add_done_callback(functools.partial(self._forget, monitor.id))
        return len(due)

    def _forget(self, monitor_id: uuid.UUID, _task: asyncio.Task[None]) -> None:
        self._in_flight.pop(monitor_id, None)
        self._slot_free.set()

    async def wait_idle(self, grace_s: float | None = None, *, cancel_rest: bool = False) -> None:
        """Wait for running checks to finish (used at shutdown and in tests)."""
        pending = list(self._in_flight.values())
        if not pending:
            return
        _, still_running = await asyncio.wait(pending, timeout=grace_s)
        if cancel_rest:
            for task in still_running:
                task.cancel()  # its monitor stays due and is checked again after restart
            await asyncio.gather(*still_running, return_exceptions=True)

    async def _check(self, monitor: Monitor) -> None:
        """Probe one monitor and save the result. Never raises: a monitor cannot hurt the others."""
        try:
            started_at = datetime.now(UTC)
            result = await run_probe(
                ProbeRequest(
                    url=monitor.url,
                    timeout_ms=monitor.timeout_ms,
                    expected_status=monitor.expected_status,
                    body_contains=monitor.body_contains,
                ),
                resolver=self.resolver,
                policy=self.policy,
                transport=self.transport,
            )
            async with self.sessions() as session:
                action = await record_result(session, monitor, result, started_at=started_at)
            log.info(
                "checked monitor=%s outcome=%s class=%s",
                monitor.id,
                result.outcome.value,
                result.failure_class.value if result.failure_class else "-",
            )
            if action is not Action.NONE and monitor.webhook_url:
                await notify(
                    monitor,
                    action,
                    result,
                    policy=self.policy,
                    resolver=self.resolver,
                    transport=self.transport,
                )
        except DB_ERRORS as exc:
            # Nothing was saved, so the monitor is still due and will be checked again.
            log.error("could not save result monitor=%s: %s", monitor.id, exc)
        except Exception:
            log.exception("check failed monitor=%s", monitor.id)

    async def _cleanup_if_due(self) -> None:
        """Delete old check history and expired sessions (at most once an hour)."""
        now = datetime.now(UTC)
        if now - self._last_cleanup < CLEANUP_EVERY:
            return
        self._last_cleanup = now
        cutoff = now - timedelta(days=self.retention_days)
        async with self.sessions() as session:
            await session.execute(delete(CheckRun).where(CheckRun.started_at < cutoff))
            await session.execute(delete(Session).where(Session.expires_at < now))
            await session.commit()


async def record_result(
    session: AsyncSession, monitor: Monitor, result: ProbeResult, *, started_at: datetime
) -> Action:
    """Save one result, apply the incident rule, schedule the next check, all in one commit.

    Returns what happened to the incident (opened, resolved, or nothing).
    """
    # Lock the row so a concurrent edit or delete waits for us.
    current = await session.get(Monitor, monitor.id, with_for_update=True)
    if current is None:  # deleted while we were probing
        return Action.NONE

    session.add(
        CheckRun(
            monitor_id=current.id,
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

    decision = evaluate(
        status=current.status,
        consecutive_failures=current.consecutive_failures,
        passed=result.failure_class is None,
        failure_threshold=current.failure_threshold,
    )
    now = datetime.now(UTC)
    if decision.action is Action.OPEN:
        failure_class = result.failure_class.value if result.failure_class else "ERROR"
        session.add(Incident(monitor_id=current.id, opened_at=now, failure_class=failure_class))
    elif decision.action is Action.RESOLVE:
        open_incident = await session.scalar(
            select(Incident).where(
                Incident.monitor_id == current.id, Incident.resolved_at.is_(None)
            )
        )
        if open_incident is not None:
            open_incident.resolved_at = now

    current.status = decision.status
    current.consecutive_failures = decision.consecutive_failures
    current.last_checked_at = now
    current.next_run_at = func.now() + timedelta(seconds=current.interval_seconds)
    await session.commit()
    return decision.action
