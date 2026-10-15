"""Incident state machine (ADR-007). Pure: no I/O, no clock, no database.

`evaluate` takes the monitor's current health state plus one run outcome and returns the new
state and the transitions the service layer must persist. INCONCLUSIVE changes nothing: a
problem on our side is not evidence about the target.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.probe.types import FailureClass, Outcome


class TransitionKind(StrEnum):
    OPEN = "OPEN"
    CLASS_CHANGED = "CLASS_CHANGED"
    RECOVERING = "RECOVERING"
    RESOLVE = "RESOLVE"


@dataclass(frozen=True)
class Transition:
    kind: TransitionKind
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Thresholds:
    failure: int = 3
    recovery: int = 2


@dataclass(frozen=True)
class HealthState:
    health: str = "UNKNOWN"
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    incident_open: bool = False
    # The current run of consecutive FAILs (basis for backdating opened_at).
    streak_first_run_id: int | None = None
    streak_first_at: datetime | None = None
    streak_class_counts: Mapping[str, int] = field(default_factory=dict)
    # Failure classes seen while an incident is open (basis for the dominant class).
    incident_class_counts: Mapping[str, int] = field(default_factory=dict)


def dominant_class(counts: Mapping[str, int]) -> str | None:
    """Most frequent class; ties broken alphabetically so the result is deterministic."""
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _bump(counts: Mapping[str, int], key: str) -> dict[str, int]:
    out = dict(counts)
    out[key] = out.get(key, 0) + 1
    return out


def evaluate(
    state: HealthState,
    *,
    outcome: Outcome,
    failure_class: FailureClass | None,
    run_id: int,
    at: datetime,
    thresholds: Thresholds,
) -> tuple[HealthState, list[Transition]]:
    if outcome is Outcome.INCONCLUSIVE:
        return state, []

    if outcome is Outcome.FAIL:
        return _on_failure(state, failure_class, run_id, at, thresholds)

    # PASS or DEGRADED: the endpoint answered acceptably.
    ok_health = "UP" if outcome is Outcome.PASS else "DEGRADED"
    successes = state.consecutive_successes + 1
    base = replace(
        state,
        consecutive_failures=0,
        consecutive_successes=successes,
        streak_first_run_id=None,
        streak_first_at=None,
        streak_class_counts={},
    )
    if not state.incident_open:
        return replace(base, health=ok_health), []
    if successes >= thresholds.recovery:
        resolved = replace(base, health=ok_health, incident_open=False, incident_class_counts={})
        return resolved, [Transition(TransitionKind.RESOLVE, {"consecutive_successes": successes})]
    recovering = replace(base, health="DOWN")
    return recovering, [
        Transition(
            TransitionKind.RECOVERING,
            {"consecutive_successes": successes, "needed": thresholds.recovery},
        )
    ]


def _on_failure(
    state: HealthState,
    failure_class: FailureClass | None,
    run_id: int,
    at: datetime,
    thresholds: Thresholds,
) -> tuple[HealthState, list[Transition]]:
    fc = (failure_class or FailureClass.PROBE_INTERNAL_ERROR).value
    starting = state.consecutive_failures == 0
    failures = state.consecutive_failures + 1
    streak_counts = _bump({} if starting else state.streak_class_counts, fc)
    new = replace(
        state,
        consecutive_failures=failures,
        consecutive_successes=0,
        streak_first_run_id=run_id if starting else state.streak_first_run_id,
        streak_first_at=at if starting else state.streak_first_at,
        streak_class_counts=streak_counts,
    )

    if state.incident_open:
        before = dominant_class(state.incident_class_counts)
        counts = _bump(state.incident_class_counts, fc)
        new = replace(new, health="DOWN", incident_class_counts=counts)
        after = dominant_class(counts)
        if before != after:
            return new, [Transition(TransitionKind.CLASS_CHANGED, {"from": before, "to": after})]
        return new, []

    if failures >= thresholds.failure:
        new = replace(new, health="DOWN", incident_open=True, incident_class_counts=streak_counts)
        return new, [
            Transition(
                TransitionKind.OPEN,
                {
                    "failure_count": failures,
                    "threshold": thresholds.failure,
                    "dominant_failure_class": dominant_class(streak_counts),
                    "first_failure_run_id": new.streak_first_run_id,
                    "opened_at": new.streak_first_at,
                },
            )
        ]
    return new, []
