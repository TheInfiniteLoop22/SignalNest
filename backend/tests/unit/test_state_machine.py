import pytest

from app.incidents.state_machine import Action, evaluate


def step(status: str, failures: int, passed: bool, threshold: int = 3):  # type: ignore[no-untyped-def]
    d = evaluate(
        status=status, consecutive_failures=failures, passed=passed, failure_threshold=threshold
    )
    return d.status, d.consecutive_failures, d.action


def test_first_pass_marks_the_monitor_up() -> None:
    assert step("UNKNOWN", 0, True) == ("UP", 0, Action.NONE)


def test_failures_below_threshold_do_not_open_an_incident() -> None:
    assert step("UP", 0, False) == ("UP", 1, Action.NONE)
    assert step("UP", 1, False) == ("UP", 2, Action.NONE)


def test_threshold_failure_opens_exactly_once() -> None:
    assert step("UP", 2, False) == ("DOWN", 3, Action.OPEN)
    assert step("DOWN", 3, False) == ("DOWN", 4, Action.NONE)  # already open: no second incident


def test_a_pass_resets_the_streak() -> None:
    assert step("UP", 2, True) == ("UP", 0, Action.NONE)
    # ...so the next outage needs a full run of failures again.
    assert step("UP", 0, False) == ("UP", 1, Action.NONE)


def test_first_pass_after_down_resolves() -> None:
    assert step("DOWN", 7, True) == ("UP", 0, Action.RESOLVE)


@pytest.mark.parametrize("threshold", [1, 5])
def test_threshold_is_respected(threshold: int) -> None:
    status, failures = "UP", 0
    actions = []
    for _ in range(threshold):
        status, failures, action = step(status, failures, False, threshold)
        actions.append(action)
    assert actions == [Action.NONE] * (threshold - 1) + [Action.OPEN]
