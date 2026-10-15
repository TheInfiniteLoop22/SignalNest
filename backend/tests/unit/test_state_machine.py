from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.incidents.state_machine import (
    HealthState,
    Thresholds,
    TransitionKind,
    dominant_class,
    evaluate,
)
from app.probe.types import FailureClass, Outcome

T0 = datetime(2026, 1, 1, tzinfo=UTC)
F, P, D, I = Outcome.FAIL, Outcome.PASS, Outcome.DEGRADED, Outcome.INCONCLUSIVE  # noqa: E741
RT = FailureClass.READ_TIMEOUT


def drive(
    outcomes: list[Outcome | tuple[Outcome, FailureClass]],
    thresholds: Thresholds | None = None,
    state: HealthState | None = None,
) -> tuple[HealthState, list[list[TransitionKind]]]:
    """Feed outcomes in order; return final state and the transition kinds per step."""
    state = state or HealthState()
    thresholds = thresholds or Thresholds(3, 2)
    steps: list[list[TransitionKind]] = []
    for i, item in enumerate(outcomes, start=1):
        outcome, fc = item if isinstance(item, tuple) else (item, RT if item is F else None)
        state, transitions = evaluate(
            state,
            outcome=outcome,
            failure_class=fc,
            run_id=i,
            at=T0 + timedelta(seconds=30 * i),
            thresholds=thresholds,
        )
        steps.append([t.kind for t in transitions])
    return state, steps


def test_pass_makes_unknown_up() -> None:
    s, steps = drive([P])
    assert (s.health, s.incident_open, steps) == ("UP", False, [[]])


def test_degraded_is_a_success_but_shows_degraded() -> None:
    s, _ = drive([P, D])
    assert s.health == "DEGRADED"
    assert s.consecutive_successes == 2


def test_failures_below_threshold_do_not_change_health_or_open() -> None:
    s, steps = drive([P, F, F])
    assert s.health == "UP"
    assert s.consecutive_failures == 2
    assert not s.incident_open
    assert steps == [[], [], []]


def test_opens_exactly_at_threshold_and_is_backdated_to_first_failure() -> None:
    s, steps = drive([P, F, F, F])
    assert steps[3] == [TransitionKind.OPEN]
    assert s.health == "DOWN" and s.incident_open
    # first failing run is run 2, at T0+60s
    _, transitions = evaluate(
        HealthState(
            consecutive_failures=2,
            streak_first_run_id=2,
            streak_first_at=T0 + timedelta(seconds=60),
            streak_class_counts={"READ_TIMEOUT": 2},
        ),
        outcome=F,
        failure_class=RT,
        run_id=4,
        at=T0 + timedelta(seconds=120),
        thresholds=Thresholds(3, 2),
    )
    detail = transitions[0].detail
    assert detail["opened_at"] == T0 + timedelta(seconds=60)
    assert detail["first_failure_run_id"] == 2
    assert detail["failure_count"] == 3


def test_only_one_open_transition_per_outage() -> None:
    _, steps = drive([F] * 10)
    assert sum(TransitionKind.OPEN in s for s in steps) == 1


def test_pass_in_the_middle_resets_the_failure_streak() -> None:
    s, steps = drive([F, F, P, F, F])
    assert not s.incident_open
    assert all(not step for step in steps)


def test_recovering_then_resolve_after_recovery_threshold() -> None:
    s, steps = drive([F, F, F, P, P])
    assert steps[3] == [TransitionKind.RECOVERING]
    assert steps[4] == [TransitionKind.RESOLVE]
    assert s.health == "UP" and not s.incident_open
    assert s.incident_class_counts == {}


def test_failure_during_recovery_restarts_recovery_and_keeps_incident() -> None:
    s, steps = drive([F, F, F, P, F, P, P])
    assert steps[3] == [TransitionKind.RECOVERING]
    assert steps[4] == []  # same incident; no second OPEN
    assert steps[6] == [TransitionKind.RESOLVE]
    assert sum(TransitionKind.OPEN in x for x in steps) == 1
    assert not s.incident_open


def test_degraded_counts_toward_recovery_and_resolves_to_degraded() -> None:
    s, steps = drive([F, F, F, D, D])
    assert steps[4] == [TransitionKind.RESOLVE]
    assert s.health == "DEGRADED"


@pytest.mark.parametrize("n", [0, 1, 2, 10])
def test_inconclusive_changes_nothing_ac9(n: int) -> None:
    s0, _ = drive([F, F])
    s1, steps = drive([I] * n, state=s0)
    assert s1 == s0
    assert all(not step for step in steps)


def test_inconclusive_does_not_break_a_failure_streak() -> None:
    s, steps = drive([F, I, F, I, F])
    assert s.incident_open
    assert steps[4] == [TransitionKind.OPEN]


def test_inconclusive_does_not_count_as_recovery() -> None:
    s, steps = drive([F, F, F, I, I, I])
    assert s.incident_open
    assert all(not x for x in steps[3:])


@pytest.mark.parametrize(("failure", "recovery"), [(1, 1), (3, 2), (10, 10)])
def test_thresholds_are_respected(failure: int, recovery: int) -> None:
    th = Thresholds(failure, recovery)
    s, steps = drive([F] * failure, th)
    assert s.incident_open
    assert steps[-1] == [TransitionKind.OPEN]
    assert all(not x for x in steps[:-1])
    s, steps = drive([P] * recovery, th, state=s)
    assert not s.incident_open
    assert steps[-1] == [TransitionKind.RESOLVE]


def test_dominant_class_tracks_and_reports_changes() -> None:
    seq = [
        (F, FailureClass.CONNECTION_REFUSED),
        (F, FailureClass.CONNECTION_REFUSED),
        (F, FailureClass.CONNECTION_REFUSED),  # opens, dominant refused
        (F, FailureClass.READ_TIMEOUT),
        (F, FailureClass.READ_TIMEOUT),
        (F, FailureClass.READ_TIMEOUT),  # 3 v 3 -> alphabetical tie-break keeps CONNECTION_REFUSED
        (F, FailureClass.READ_TIMEOUT),  # 4 v 3 -> changes
    ]
    s, steps = drive(seq)
    assert steps[2] == [TransitionKind.OPEN]
    assert steps[5] == []
    assert steps[6] == [TransitionKind.CLASS_CHANGED]
    assert dominant_class(s.incident_class_counts) == "READ_TIMEOUT"


def test_state_is_not_mutated() -> None:
    s0 = HealthState()
    snapshot = replace(s0)
    evaluate(s0, outcome=F, failure_class=RT, run_id=1, at=T0, thresholds=Thresholds())
    assert s0 == snapshot


def test_dominant_class_tie_break_is_deterministic() -> None:
    assert dominant_class({"B": 2, "A": 2}) == "A"
    assert dominant_class({}) is None
