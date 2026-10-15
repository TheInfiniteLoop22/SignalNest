"""TX2: record the run, apply incident effects, release the lease.

Idempotent (unique slot key) and fenced (only the current lease owner touches monitor state).
Everything below happens in ONE short transaction and never waits on the network, so a crash
can never leave a run recorded without its incident effect, or the reverse (docs/04 s4.5).
"""

import json
import logging
import random
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CheckRun
from app.incidents.state_machine import (
    HealthState,
    Thresholds,
    Transition,
    TransitionKind,
    dominant_class,
    evaluate,
)
from app.probe.types import Outcome, ProbeResult
from app.services.run_record import build_run_values
from app.worker.claim import Claim
from app.worker.schedule import compute_next_run_at

log = logging.getLogger(__name__)

_EVENT_KIND = {
    TransitionKind.OPEN: "OPENED",
    TransitionKind.CLASS_CHANGED: "FAILURE_CLASS_CHANGED",
    TransitionKind.RECOVERING: "RECOVERING",
    TransitionKind.RESOLVE: "RESOLVED",
}


class Completion(StrEnum):
    RECORDED = "recorded"  # run inserted, state advanced
    DUPLICATE = "duplicate"  # slot already had a run (we lost a race); state still advanced
    LEASE_LOST = "lease_lost"  # someone else owns the monitor now; state untouched
    MONITOR_DELETED = "monitor_deleted"


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


async def _add_event(
    session: AsyncSession,
    incident_id: uuid.UUID,
    kind: TransitionKind,
    run_id: int,
    at: datetime,
    detail: dict[str, Any],
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO incident_events (incident_id, at, kind, run_id, detail)
            VALUES (:incident, :at, :kind, :run, CAST(:detail AS jsonb))
            """
        ),
        {
            "incident": incident_id,
            "at": at,
            "kind": _EVENT_KIND[kind],
            "run": run_id,
            "detail": _json(detail),
        },
    )


async def _apply_incident_effects(
    session: AsyncSession,
    *,
    monitor_id: uuid.UUID,
    run_id: int,
    at: datetime,
    outcome: Outcome,
    new_state: HealthState,
    transitions: list[Transition],
) -> None:
    kinds = {t.kind for t in transitions}

    if TransitionKind.OPEN in kinds:
        t = next(t for t in transitions if t.kind is TransitionKind.OPEN)
        incident_id = await session.scalar(
            text(
                """
                INSERT INTO incidents
                    (monitor_id, status, opened_at, first_failure_run_id,
                     failure_count, dominant_failure_class)
                VALUES (:m, 'OPEN', :opened, :first_run, :n, :dom)
                RETURNING id
                """
            ),
            {
                "m": monitor_id,
                "opened": t.detail["opened_at"],  # backdated to the first failure of the streak
                "first_run": t.detail["first_failure_run_id"],
                "n": t.detail["failure_count"],
                "dom": t.detail["dominant_failure_class"],
            },
        )
        assert incident_id is not None
        await _add_event(session, incident_id, t.kind, run_id, at, dict(t.detail))
        return

    if not (new_state.incident_open or TransitionKind.RESOLVE in kinds):
        return

    incident_id = await session.scalar(
        text("SELECT id FROM incidents WHERE monitor_id = :m AND status = 'OPEN'"),
        {"m": monitor_id},
    )
    if incident_id is None:  # state says open but no row: should be impossible; do not crash
        log.error("incident.missing monitor_id=%s", monitor_id)
        return

    if outcome is Outcome.FAIL and new_state.incident_open:
        await session.execute(
            text(
                """
                UPDATE incidents
                SET failure_count = failure_count + 1, dominant_failure_class = :dom
                WHERE id = :id
                """
            ),
            {"id": incident_id, "dom": dominant_class(new_state.incident_class_counts)},
        )
    if TransitionKind.RESOLVE in kinds:
        await session.execute(
            text(
                """
                UPDATE incidents
                SET status = 'RESOLVED', resolved_at = :at, resolving_run_id = :run
                WHERE id = :id
                """
            ),
            {"id": incident_id, "at": at, "run": run_id},
        )
    for t in transitions:
        await _add_event(session, incident_id, t.kind, run_id, at, dict(t.detail))


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
        # Idempotency: UNIQUE (monitor_id, scheduled_for) turns a second completion of the
        # same slot into a no-op.
        run_id = await session.scalar(
            insert(CheckRun)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["monitor_id", "scheduled_for"])
            .returning(CheckRun.id)
        )

        db_now = now or await session.scalar(text("SELECT now()"))
        monitor = (
            (
                await session.execute(
                    text(
                        "SELECT interval_seconds, failure_threshold, recovery_threshold "
                        "FROM monitors WHERE id = :id"
                    ),
                    {"id": claim.monitor_id},
                )
            )
            .mappings()
            .first()
        )
        if monitor is None:  # deleted while we were probing
            await session.rollback()
            return Completion.MONITOR_DELETED

        # Fencing: lock the state row only if we still own the lease. If it expired and
        # another worker took over, we leave all state alone (the run row we inserted above
        # is kept as evidence; the new owner will see a conflict and still advance the slot).
        row = (
            (
                await session.execute(
                    text(
                        """
                        SELECT health, consecutive_failures, consecutive_successes,
                               incident_open, streak_first_run_id, streak_first_at,
                               streak_class_counts, incident_class_counts
                        FROM monitor_state
                        WHERE monitor_id = :id AND lease_owner = :worker
                        FOR UPDATE
                        """
                    ),
                    {"id": claim.monitor_id, "worker": worker_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            await session.commit()
            return Completion.LEASE_LOST

        state = HealthState(
            health=row["health"],
            consecutive_failures=row["consecutive_failures"],
            consecutive_successes=row["consecutive_successes"],
            incident_open=row["incident_open"],
            streak_first_run_id=row["streak_first_run_id"],
            streak_first_at=row["streak_first_at"],
            streak_class_counts=row["streak_class_counts"],
            incident_class_counts=row["incident_class_counts"],
        )
        if run_id is not None:
            state, transitions = evaluate(
                state,
                outcome=result.outcome,
                failure_class=result.failure_class,
                run_id=run_id,
                at=started_at,
                thresholds=Thresholds(
                    failure=monitor["failure_threshold"], recovery=monitor["recovery_threshold"]
                ),
            )
            await _apply_incident_effects(
                session,
                monitor_id=claim.monitor_id,
                run_id=run_id,
                at=finished_at,
                outcome=result.outcome,
                new_state=state,
                transitions=transitions,
            )

        interval_s = monitor["interval_seconds"]
        if jitter_s is None:
            jitter_s = random.uniform(0, min(5.0, interval_s * 0.1))
        next_run = compute_next_run_at(claim.scheduled_for, interval_s, db_now, jitter_s)

        await session.execute(
            text(
                """
                UPDATE monitor_state
                SET next_run_at = :next_run,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_run_id = COALESCE(:run_id, last_run_id),
                    last_run_at = CASE WHEN :run_id IS NULL THEN last_run_at ELSE :finished END,
                    health = :health,
                    consecutive_failures = :cf,
                    consecutive_successes = :cs,
                    incident_open = :open,
                    streak_first_run_id = :first_run,
                    streak_first_at = :first_at,
                    streak_class_counts = CAST(:streak_counts AS jsonb),
                    incident_class_counts = CAST(:incident_counts AS jsonb)
                WHERE monitor_id = :id AND lease_owner = :worker
                """
            ),
            {
                "next_run": next_run,
                "run_id": run_id,
                "finished": finished_at,
                "health": state.health,
                "cf": state.consecutive_failures,
                "cs": state.consecutive_successes,
                "open": state.incident_open,
                "first_run": state.streak_first_run_id,
                "first_at": state.streak_first_at,
                "streak_counts": _json(dict(state.streak_class_counts)),
                "incident_counts": _json(dict(state.incident_class_counts)),
                "id": claim.monitor_id,
                "worker": worker_id,
            },
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if "foreign key" not in str(exc.orig).lower():
            raise
        # The monitor was deleted between claim and completion.
        log.info("run.discarded monitor_id=%s reason=monitor_deleted", claim.monitor_id)
        return Completion.MONITOR_DELETED

    return Completion.RECORDED if run_id is not None else Completion.DUPLICATE
