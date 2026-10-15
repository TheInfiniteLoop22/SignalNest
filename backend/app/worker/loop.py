"""The worker: poll -> claim (TX1) -> probe (no tx) -> complete (TX2)."""

import asyncio
import logging
import os
import random
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.observability import metrics
from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import ProbeRequest, Resolver, assertion_list_adapter
from app.worker import retention
from app.worker.claim import Claim, claim_due, release_leases
from app.worker.complete import Completion, complete_run

log = logging.getLogger("signalnest.worker")

VERSION = "0.0.0"
DB_ERRORS = (DBAPIError, InterfaceError, OSError)

LAG_SQL = text(
    """
    SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(s.next_run_at)), 0)
    FROM monitor_state s JOIN monitors m ON m.id = s.monitor_id
    WHERE m.is_paused = false
      AND s.next_run_at <= now()
      AND (s.lease_owner IS NULL OR s.lease_expires_at < now())
    """
)


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def request_for_claim(claim: Claim) -> ProbeRequest:
    return ProbeRequest(
        url=claim.url,
        method="HEAD" if claim.method == "HEAD" else "GET",
        timeout_ms=claim.timeout_ms,
        headers=claim.headers,
        assertions=assertion_list_adapter.validate_python(claim.assertions),
        degraded_ms=claim.degraded_ms,
    )


class Worker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        policy: SsrfPolicy,
        worker_id: str | None = None,
        max_concurrency: int = 20,
        poll_interval_s: float = 1.0,
        claim_batch_max: int = 50,
        shutdown_grace_s: float = 20.0,
        heartbeat_interval_s: float = 10.0,
        jitter_s: float | None = None,  # None: random per run; tests pin it
        db_backoff_min_s: float = 1.0,
        db_backoff_max_s: float = 30.0,
        retention_every_s: int = 3600,
        retention_days: int = 14,
        resolver: Resolver | None = None,
        transport: Any = None,
    ) -> None:
        self.sessions = sessions
        self.policy = policy
        self.worker_id = worker_id or default_worker_id()
        self.max_concurrency = max_concurrency
        self.poll_interval_s = poll_interval_s
        self.claim_batch_max = claim_batch_max
        self.shutdown_grace_s = shutdown_grace_s
        self.heartbeat_interval_s = heartbeat_interval_s
        self.jitter_s = jitter_s
        self.db_backoff_min_s = db_backoff_min_s
        self.db_backoff_max_s = db_backoff_max_s
        self.retention_every_s = retention_every_s
        self.retention_days = retention_days
        self.resolver = resolver
        self.transport = transport

        self._stop = asyncio.Event()
        self._sem = asyncio.Semaphore(max_concurrency)
        self._in_flight: dict[asyncio.Task[None], uuid.UUID] = {}
        self._started_at = datetime.now(UTC)
        self._last_error: str | None = None

    # --- control --------------------------------------------------------------------

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    # --- main loop ------------------------------------------------------------------

    async def run(self) -> None:
        log.info(
            "worker.started",
            extra={"worker_id": self.worker_id, "max_concurrency": self.max_concurrency},
        )
        background = [
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
            asyncio.create_task(self._housekeeping_loop(), name="housekeeping"),
        ]
        backoff = 0.0
        try:
            while not self._stop.is_set():
                try:
                    claimed = await self._claim_once()
                    backoff = 0.0
                except DB_ERRORS as exc:
                    # Datastore trouble: nothing is claimed, nothing is half-written. Back
                    # off exponentially (with jitter) and keep the process alive.
                    metrics.DB_ERRORS.labels("claim").inc()
                    self._last_error = f"{type(exc).__name__}: {exc}"[:500]
                    backoff = min(self.db_backoff_max_s, max(self.db_backoff_min_s, backoff * 2))
                    delay = backoff * random.uniform(0.75, 1.25)
                    log.error(
                        "db.unavailable",
                        extra={
                            "worker_id": self.worker_id,
                            "backoff_s": round(delay, 2),
                            "error": self._last_error,
                        },
                    )
                    await self._sleep(delay)
                    continue
                # A full batch means more work is probably waiting: poll again immediately.
                if claimed < self.claim_batch_max or claimed == 0:
                    await self._sleep(self.poll_interval_s)
        finally:
            for t in background:
                t.cancel()
            await self._drain()
            metrics.IN_FLIGHT.labels(self.worker_id).set(0)
            log.info("worker.stopped", extra={"worker_id": self.worker_id})

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _claim_once(self) -> int:
        free = self.max_concurrency - len(self._in_flight)
        limit = min(free, self.claim_batch_max)
        if limit <= 0:
            return 0
        async with self.sessions() as session:
            claims = await claim_due(session, worker_id=self.worker_id, limit=limit)
        for claim in claims:
            task = asyncio.create_task(self._execute(claim), name=f"probe-{claim.monitor_id}")
            self._in_flight[task] = claim.monitor_id
            task.add_done_callback(self._probe_done)
        if claims:
            metrics.CLAIM_BATCH.observe(len(claims))
            metrics.IN_FLIGHT.labels(self.worker_id).set(len(self._in_flight))
            log.debug(
                "worker.claimed",
                extra={
                    "n": len(claims),
                    "in_flight": len(self._in_flight),
                    "worker_id": self.worker_id,
                },
            )
        return len(claims)

    def _probe_done(self, task: "asyncio.Task[None]") -> None:
        self._in_flight.pop(task, None)
        metrics.IN_FLIGHT.labels(self.worker_id).set(len(self._in_flight))

    # --- one probe ------------------------------------------------------------------

    async def _execute(self, claim: Claim) -> None:
        async with self._sem:
            started_at = datetime.now(UTC)
            # No database transaction is open here: the network call may take seconds.
            request = request_for_claim(claim)
            result = await run_probe(
                request, resolver=self.resolver, policy=self.policy, transport=self.transport
            )
            finished_at = datetime.now(UTC)
        try:
            async with self.sessions() as session:
                outcome = await complete_run(
                    session,
                    claim,
                    result,
                    worker_id=self.worker_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    jitter_s=self.jitter_s,
                )
        except DB_ERRORS as exc:
            # The lease will expire and the slot will be probed again; nothing is corrupted.
            metrics.DB_ERRORS.labels("complete").inc()
            self._last_error = f"{type(exc).__name__}: {exc}"[:500]
            log.error(
                "run.complete_failed",
                extra={
                    "monitor_id": str(claim.monitor_id),
                    "worker_id": self.worker_id,
                    "error": self._last_error,
                },
            )
            return
        fc = result.failure_class.value if result.failure_class else ""
        metrics.RUNS_TOTAL.labels(result.outcome.value, fc).inc()
        metrics.PROBE_DURATION.labels(result.outcome.value).observe(
            (finished_at - started_at).total_seconds()
        )
        log.info(
            "run.completed",
            extra={
                "monitor_id": str(claim.monitor_id),
                "worker_id": self.worker_id,
                "outcome": result.outcome.value,
                "failure_class": result.failure_class.value if result.failure_class else None,
                "duration_ms": result.final.total_ms,
                "attempts": len(result.attempts),
                "completion": outcome.value if isinstance(outcome, Completion) else str(outcome),
            },
        )

    # --- heartbeat & housekeeping ---------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                async with self.sessions() as session:
                    await session.execute(
                        text(
                            """
                            INSERT INTO worker_heartbeats
                                (worker_id, started_at, last_seen_at, version,
                                 in_flight, last_error)
                            VALUES (:id, :started, now(), :version, :in_flight, :err)
                            ON CONFLICT (worker_id) DO UPDATE
                            SET last_seen_at = now(), in_flight = :in_flight, last_error = :err
                            """
                        ),
                        {
                            "id": self.worker_id,
                            "started": self._started_at,
                            "version": VERSION,
                            "in_flight": len(self._in_flight),
                            "err": self._last_error,
                        },
                    )
                    lag = await session.scalar(LAG_SQL)
                    await session.commit()
                metrics.SCHEDULER_LAG.set(float(lag or 0))
            except DB_ERRORS as exc:
                metrics.DB_ERRORS.labels("heartbeat").inc()
                log.warning("worker.heartbeat_failed", extra={"error": str(exc)[:300]})
            await asyncio.sleep(self.heartbeat_interval_s)

    async def _housekeeping_loop(self) -> None:
        # Staggered start so a fleet restarting together does not all try at once.
        await asyncio.sleep(random.uniform(1, 10))
        while True:
            try:
                await retention.run_if_due(
                    self.sessions,
                    worker_id=self.worker_id,
                    every_s=self.retention_every_s,
                    retention_days=self.retention_days,
                )
            except DB_ERRORS as exc:
                metrics.DB_ERRORS.labels("retention").inc()
                log.warning("retention.skipped", extra={"error": str(exc)[:300]})
            await asyncio.sleep(min(self.retention_every_s, 300))

    # --- shutdown -------------------------------------------------------------------

    async def _drain(self) -> None:
        """Stop claiming (already true), let in-flight probes finish within the grace period,
        then cancel the rest and hand their leases back."""
        pending = set(self._in_flight)
        if not pending:
            return
        log.info(
            "worker.draining",
            extra={"in_flight": len(pending), "grace_s": self.shutdown_grace_s},
        )
        _, still_running = await asyncio.wait(pending, timeout=self.shutdown_grace_s)
        if not still_running:
            return
        abandoned = [self._in_flight[t] for t in still_running if t in self._in_flight]
        for task in still_running:
            task.cancel()
        await asyncio.gather(*still_running, return_exceptions=True)
        try:
            async with self.sessions() as session:
                n = await release_leases(session, worker_id=self.worker_id, monitor_ids=abandoned)
            log.warning(
                "worker.abandoned",
                extra={"n": len(abandoned), "leases_released": n, "worker_id": self.worker_id},
            )
        except DB_ERRORS as exc:
            # Leases simply expire; another worker will reclaim them.
            log.error("worker.release_failed", extra={"error": str(exc)[:300]})
