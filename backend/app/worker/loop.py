"""The worker: poll -> claim (TX1) -> probe (no transaction) -> record (TX2)."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import ProbeRequest, Resolver
from app.worker.queue import Claim, claim_due, record_result

log = logging.getLogger("signalnest.worker")

DB_ERRORS = (DBAPIError, InterfaceError, OSError)


class Worker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        policy: SsrfPolicy,
        max_concurrency: int = 20,
        poll_interval_s: float = 1.0,
        claim_batch_max: int = 50,
        shutdown_grace_s: float = 20.0,
        db_retry_s: float = 5.0,
        resolver: Resolver | None = None,
        transport: Any = None,
    ) -> None:
        self.sessions = sessions
        self.policy = policy
        self.max_concurrency = max_concurrency
        self.poll_interval_s = poll_interval_s
        self.claim_batch_max = claim_batch_max
        self.shutdown_grace_s = shutdown_grace_s
        self.db_retry_s = db_retry_s
        self.resolver = resolver
        self.transport = transport

        self._stop = asyncio.Event()
        self._in_flight: dict[asyncio.Task[None], uuid.UUID] = {}

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    async def run(self) -> None:
        log.info("worker started max_concurrency=%d", self.max_concurrency)
        try:
            while not self._stop.is_set():
                try:
                    claimed = await self._claim_once()
                except DB_ERRORS as exc:
                    # Nothing is claimed and nothing is half-written; try again shortly.
                    log.error("database unavailable, retrying in %.0fs: %s", self.db_retry_s, exc)
                    await self._sleep(self.db_retry_s)
                    continue
                # A full batch means more work is probably waiting: poll again immediately.
                if claimed < self.claim_batch_max:
                    await self._sleep(self.poll_interval_s)
        finally:
            await self._drain()
            log.info("worker stopped")

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _claim_once(self) -> int:
        limit = min(self.max_concurrency - len(self._in_flight), self.claim_batch_max)
        if limit <= 0:
            return 0
        async with self.sessions() as session:
            claims = await claim_due(session, limit=limit)
        for claim in claims:
            task = asyncio.create_task(self._execute(claim), name=f"probe-{claim.id}")
            self._in_flight[task] = claim.id
            task.add_done_callback(lambda t: self._in_flight.pop(t, None))
        return len(claims)

    async def _execute(self, claim: Claim) -> None:
        started_at = datetime.now(UTC)
        # No database transaction is open here: the network call may take seconds.
        result = await run_probe(
            ProbeRequest(
                url=claim.url,
                timeout_ms=claim.timeout_ms,
                expected_status=claim.expected_status,
                body_contains=claim.body_contains,
            ),
            resolver=self.resolver,
            policy=self.policy,
            transport=self.transport,
        )
        try:
            async with self.sessions() as session:
                await record_result(session, claim, result, started_at=started_at)
        except DB_ERRORS as exc:
            # The lease will expire and the monitor will be probed again; nothing is corrupted.
            log.error("could not record run monitor=%s: %s", claim.id, exc)
            return
        log.info(
            "probed monitor=%s outcome=%s class=%s total_ms=%d",
            claim.id,
            result.outcome.value,
            result.failure_class.value if result.failure_class else "-",
            result.total_ms,
        )

    async def _drain(self) -> None:
        """Let in-flight probes finish within the grace period, then cancel the rest. Their
        leases simply expire, and another worker re-probes those monitors."""
        pending = set(self._in_flight)
        if not pending:
            return
        log.info("draining %d in-flight probes (grace %.0fs)", len(pending), self.shutdown_grace_s)
        _, still_running = await asyncio.wait(pending, timeout=self.shutdown_grace_s)
        for task in still_running:
            task.cancel()
        await asyncio.gather(*still_running, return_exceptions=True)
