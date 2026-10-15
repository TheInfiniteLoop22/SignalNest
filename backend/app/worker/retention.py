"""Housekeeping: delete old runs / sessions / stale heartbeats in small batches.

Each batch is its own short transaction (a huge DELETE would hold locks and bloat WAL).
Runs referenced by an incident are never deleted: the FK is RESTRICT, and the job filters
them out so it does not even try.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger("signalnest.retention")

BATCH = 5000
JOB = "retention"

_DELETE_RUNS = text(
    """
    DELETE FROM check_runs
    WHERE id IN (
        SELECT r.id FROM check_runs r
        WHERE r.started_at < :cutoff
          AND NOT EXISTS (SELECT 1 FROM incidents i
                          WHERE i.first_failure_run_id = r.id OR i.resolving_run_id = r.id)
          AND NOT EXISTS (SELECT 1 FROM incident_events e WHERE e.run_id = r.id)
        ORDER BY r.id
        LIMIT :batch
    )
    """
)
_COUNT_RUNS = text(
    """
    SELECT count(*) FROM check_runs r
    WHERE r.started_at < :cutoff
      AND NOT EXISTS (SELECT 1 FROM incidents i
                      WHERE i.first_failure_run_id = r.id OR i.resolving_run_id = r.id)
      AND NOT EXISTS (SELECT 1 FROM incident_events e WHERE e.run_id = r.id)
    """
)


@dataclass(frozen=True)
class RetentionResult:
    runs: int
    sessions: int
    heartbeats: int

    @property
    def total(self) -> int:
        return self.runs + self.sessions + self.heartbeats


async def preview(session: AsyncSession, *, retention_days: int, now: datetime) -> int:
    cutoff = now - timedelta(days=retention_days)
    return int(await session.scalar(_COUNT_RUNS, {"cutoff": cutoff}) or 0)


async def _batched(
    sessions: async_sessionmaker[AsyncSession], stmt: object, params: dict[str, object]
) -> int:
    total = 0
    while True:
        async with sessions() as s:
            result = await s.execute(stmt, params)  # type: ignore[call-overload]
            await s.commit()
            n = int(result.rowcount or 0)
        total += n
        if n < BATCH:
            return total


async def run_retention(
    sessions: async_sessionmaker[AsyncSession], *, retention_days: int, now: datetime
) -> RetentionResult:
    cutoff = now - timedelta(days=retention_days)
    runs = await _batched(sessions, _DELETE_RUNS, {"cutoff": cutoff, "batch": BATCH})
    session_cutoff = now - timedelta(days=7)
    stale = await _batched(
        sessions,
        text(
            """
            DELETE FROM sessions WHERE id IN (
                SELECT id FROM sessions
                WHERE expires_at < :c OR (revoked_at IS NOT NULL AND revoked_at < :c)
                LIMIT :batch)
            """
        ),
        {"c": session_cutoff, "batch": BATCH},
    )
    hb = await _batched(
        sessions,
        text(
            """
            DELETE FROM worker_heartbeats WHERE worker_id IN (
                SELECT worker_id FROM worker_heartbeats
                WHERE last_seen_at < :c LIMIT :batch)
            """
        ),
        {"c": now - timedelta(hours=1), "batch": BATCH},
    )
    return RetentionResult(runs=runs, sessions=stale, heartbeats=hb)


async def run_if_due(
    sessions: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    every_s: int,
    retention_days: int,
) -> RetentionResult | None:
    """Run retention at most once per `every_s` across ALL workers.

    The claim is an INSERT into job_runs guarded by an advisory lock, so two workers that
    wake at the same moment cannot both pass the "nothing ran recently" check.
    """
    async with sessions() as s:
        await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('signalnest.retention'))"))
        job_id = await s.scalar(
            text(
                """
                INSERT INTO job_runs (job_name, started_at, worker_id)
                SELECT :job, now(), :w
                WHERE NOT EXISTS (
                    SELECT 1 FROM job_runs
                    WHERE job_name = :job AND started_at > now() - make_interval(secs => :every))
                RETURNING id
                """
            ),
            {"job": JOB, "w": worker_id, "every": every_s},
        )
        now = await s.scalar(text("SELECT now()"))
        await s.commit()
    if job_id is None:
        return None

    try:
        result = await run_retention(sessions, retention_days=retention_days, now=now)
        error = None
    except Exception as exc:  # recorded for the System page, then re-raised to the caller log
        error = f"{type(exc).__name__}: {exc}"[:500]
        result = RetentionResult(0, 0, 0)
        log.exception("retention.failed")
    async with sessions() as s:
        await s.execute(
            text(
                "UPDATE job_runs SET finished_at = now(), rows_affected = :n, error = :e "
                "WHERE id = :id"
            ),
            {"n": result.total, "e": error, "id": job_id},
        )
        await s.commit()
    log.info(
        "retention.completed",
        extra={
            "runs_deleted": result.runs,
            "sessions_deleted": result.sessions,
            "heartbeats_deleted": result.heartbeats,
            "error": error,
        },
    )
    return result
