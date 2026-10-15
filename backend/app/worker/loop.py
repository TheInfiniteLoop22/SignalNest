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

from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import ProbeRequest, Resolver, assertion_list_adapter
from app.worker.claim import Claim, claim_due, release_leases
from app.worker.complete import Completion, complete_run

log = logging.getLogger("signalnest.worker")

VERSION = "0.0.0"
DB_ERRORS = (DBAPIError, InterfaceError, OSError)


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
            "worker.started worker_id=%s max_concurrency=%d", self.worker_id, self.max_concurrency
        )
        heartbeat = asyncio.create_task(self._heartbeat_loop(), name="heartbeat")
        backoff = 0.0
        try:
            while not self._stop.is_set():
                try:
                    claimed = await self._claim_once()
                    backoff = 0.0
                except DB_ERRORS as exc:
                    # Datastore trouble: nothing is claimed, nothing is half-written. Back
                    # off exponentially (with jitter) and keep the process alive.
                    self._last_error = f"{type(exc).__name__}: {exc}"[:500]
                    backoff = min(30.0, max(1.0, backoff * 2))
                    delay = backoff * random.uniform(0.75, 1.25)
                    log.error("worker.db_error backoff_s=%.1f error=%s", delay, self._last_error)
                    await self._sleep(delay)
                    continue
                # A full batch means more work is probably waiting: poll again immediately.
                if claimed < self.claim_batch_max or claimed == 0:
                    await self._sleep(self.poll_interval_s)
        finally:
            heartbeat.cancel()
            await self._drain()
            log.info("worker.stopped worker_id=%s", self.worker_id)

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
            task.add_done_callback(lambda t: self._in_flight.pop(t, None))
        if claims:
            log.debug("worker.claimed n=%d in_flight=%d", len(claims), len(self._in_flight))
        return len(claims)

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
            self._last_error = f"{type(exc).__name__}: {exc}"[:500]
            log.error(
                "run.complete_failed monitor_id=%s error=%s", claim.monitor_id, self._last_error
            )
            return
        log.info(
            "run.completed monitor_id=%s worker_id=%s outcome=%s failure_class=%s "
            "duration_ms=%d attempts=%d completion=%s",
            claim.monitor_id,
            self.worker_id,
            result.outcome.value,
            result.failure_class.value if result.failure_class else None,
            result.final.total_ms,
            len(result.attempts),
            outcome.value if isinstance(outcome, Completion) else outcome,
        )

    # --- heartbeat ------------------------------------------------------------------

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
                    await session.commit()
            except DB_ERRORS as exc:
                log.warning("worker.heartbeat_failed error=%s", exc)
            await asyncio.sleep(self.heartbeat_interval_s)

    # --- shutdown -------------------------------------------------------------------

    async def _drain(self) -> None:
        """Stop claiming (already true), let in-flight probes finish within the grace period,
        then cancel the rest and hand their leases back."""
        pending = set(self._in_flight)
        if not pending:
            return
        log.info("worker.draining in_flight=%d grace_s=%.0f", len(pending), self.shutdown_grace_s)
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
            log.warning("worker.abandoned n=%d leases_released=%d", len(abandoned), n)
        except DB_ERRORS as exc:
            # Leases simply expire; another worker will reclaim them.
            log.error("worker.release_failed error=%s", exc)
