"""TX2: record the run and release the lease. Idempotent and fenced (docs/04 section 4.1)."""

import logging
import random
from datetime import datetime
from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CheckRun
from app.probe.types import Outcome, ProbeResult
from app.services.run_record import HEALTH_BY_OUTCOME, build_run_values
from app.worker.claim import Claim
from app.worker.schedule import compute_next_run_at

log = logging.getLogger(__name__)


class Completion(StrEnum):
    RECORDED = "recorded"  # run inserted, state advanced
    DUPLICATE = "duplicate"  # slot already had a run (we lost a race); state still advanced
    LEASE_LOST = "lease_lost"  # someone else owns the monitor now; nothing touched
    MONITOR_DELETED = "monitor_deleted"


async def complete_run(
    session: AsyncSession,
    claim: Claim,
    result: ProbeResult,
    *,
    worker_id: str,
    started_at: datetime,
    finished_at: datetime,
    now: datetime | None = None,
    jitter_s: float | None = None,
) -> Completion:
    """One short transaction. No network I/O happens here or while it is open."""
    values = build_run_values(
        monitor_id=claim.monitor_id,
        scheduled_for=claim.scheduled_for,
        started_at=started_at,
        finished_at=finished_at,
        result=result,
        worker_id=worker_id,
        trigger="schedule",
    )
    try:
        # Idempotency: UNIQUE (monitor_id, scheduled_for) makes a second completion of the
        # same slot a no-op.
        inserted_id = await session.scalar(
            insert(CheckRun)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["monitor_id", "scheduled_for"])
            .returning(CheckRun.id)
        )

        db_now = now or await session.scalar(text("SELECT now()"))
        interval_s = await session.scalar(
            text("SELECT interval_seconds FROM monitors WHERE id = :id"), {"id": claim.monitor_id}
        )
        if interval_s is None:  # deleted while we were probing
            await session.rollback()
            return Completion.MONITOR_DELETED
        if jitter_s is None:
            jitter_s = random.uniform(0, min(5.0, interval_s * 0.1))
        next_run = compute_next_run_at(claim.scheduled_for, interval_s, db_now, jitter_s)

        # Fencing: only the current lease owner may touch the state row. If our lease expired
        # and another worker took over, rowcount is 0 and we leave everything alone.
        health = None
        if inserted_id is not None and result.outcome is not Outcome.INCONCLUSIVE:
            health = HEALTH_BY_OUTCOME[result.outcome]
        update = await session.execute(
            text(
                """
                UPDATE monitor_state
                SET next_run_at = :next_run,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_run_id = COALESCE(:run_id, last_run_id),
                    last_run_at = CASE WHEN :run_id IS NULL THEN last_run_at ELSE :finished END,
                    health = COALESCE(:health, health)
                WHERE monitor_id = :id AND lease_owner = :worker
                """
            ),
            {
                "next_run": next_run,
                "run_id": inserted_id,
                "finished": finished_at,
                "health": health,
                "id": claim.monitor_id,
                "worker": worker_id,
            },
        )
        await session.commit()
    except IntegrityError:
        # FK violation: the monitor was deleted between claim and completion.
        await session.rollback()
        log.info("run.discarded monitor_id=%s reason=monitor_deleted", claim.monitor_id)
        return Completion.MONITOR_DELETED

    if update.rowcount == 0:  # type: ignore[attr-defined]
        return Completion.LEASE_LOST
    return Completion.RECORDED if inserted_id is not None else Completion.DUPLICATE
