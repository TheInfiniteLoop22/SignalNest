"""The worker: poll -> claim (TX1) -> probe (no tx) -> complete (TX2)."""

import asyncio
import logging
import os
import random
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import ProbeRequest, Resolver, assertion_list_adapter
from app.worker.claim import Claim, claim_due, release_leases
from app.worker.complete import complete_run

log = logging.getLogger("signalnest.worker")

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
        jitter_s: float | None = None,  # None: random per run; tests pin it
        db_backoff_min_s: float = 1.0,
        db_backoff_max_s: float = 30.0,
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
        self.jitter_s = jitter_s
        self.db_backoff_min_s = db_backoff_min_s
        self.db_backoff_max_s = db_backoff_max_s
        self.resolver = resolver
        self.transport = transport

        self._stop = asyncio.Event()
        self._sem = asyncio.Semaphore(max_concurrency)
        self._in_flight: dict[asyncio.Task[None], uuid.UUID] = {}

    # --- control --------------------------------------------------------------------

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    # --- main loop ------------------------------------------------------------------

    async def run(self) -> None:
        log.info("worker started id=%s max_concurrency=%d", self.worker_id, self.max_concurrency)
        backoff = 0.0
        try:
            while not self._stop.is_set():
                try:
                    claimed = await self._claim_once()
                    backoff = 0.0
                except DB_ERRORS as exc:
                    # Datastore trouble: nothing is claimed, nothing is half-written. Back
                    # off exponentially (with jitter) and keep the process alive.
                    backoff = min(self.db_backoff_max_s, max(self.db_backoff_min_s, backoff * 2))
                    delay = backoff * random.uniform(0.75, 1.25)
                    log.error("database unavailable, retrying in %.1fs: %s", delay, exc)
                    await self._sleep(delay)
                    continue
                # A full batch means more work is probably waiting: poll again immediately.
                if claimed < self.claim_batch_max or claimed == 0:
                    await self._sleep(self.poll_interval_s)
        finally:
            await self._drain()
            log.info("worker stopped id=%s", self.worker_id)

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
        return len(claims)

    # --- one probe ------------------------------------------------------------------

    async def _execute(self, claim: Claim) -> None:
        async with self._sem:
            started_at = datetime.now(UTC)
            # No database transaction is open here: the network call may take seconds.
            result = await run_probe(
                request_for_claim(claim),
                resolver=self.resolver,
                policy=self.policy,
                transport=self.transport,
            )
            finished_at = datetime.now(UTC)
        try:
            async with self.sessions() as session:
                completion = await complete_run(
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
            log.error("could not record run monitor=%s: %s", claim.monitor_id, exc)
            return
        log.info(
            "run completed monitor=%s outcome=%s class=%s total_ms=%d (%s)",
            claim.monitor_id,
            result.outcome.value,
            result.failure_class.value if result.failure_class else "-",
            result.final.total_ms,
            completion.value,
        )

    # --- shutdown -------------------------------------------------------------------

    async def _drain(self) -> None:
        """Stop claiming (already true), let in-flight probes finish within the grace period,
        then cancel the rest and hand their leases back."""
        pending = set(self._in_flight)
        if not pending:
            return
        log.info("draining %d in-flight probes (grace %.0fs)", len(pending), self.shutdown_grace_s)
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
            log.warning("abandoned %d probes, released %d leases", len(abandoned), n)
        except DB_ERRORS as exc:
            # Leases simply expire; another worker will reclaim them.
            log.error("could not release leases: %s", exc)
