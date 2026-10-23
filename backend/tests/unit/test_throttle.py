import pytest

from app.api import throttle as throttle_module
from app.api.throttle import LoginThrottle


class Clock:
    """A controllable replacement for time.monotonic."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(throttle_module.time, "monotonic", c)
    return c


def fail(t: LoginThrottle, email: str, times: int) -> None:
    for _ in range(times):
        t.record_failure(email)


def test_locks_after_the_maximum_number_of_failures(clock: Clock) -> None:
    t = LoginThrottle(max_failures=3, window_s=900)
    fail(t, "a@x.test", 2)
    assert t.locked_for("a@x.test") == 0
    fail(t, "a@x.test", 1)
    assert t.locked_for("a@x.test") > 0


def test_retry_after_counts_down_to_when_the_oldest_failure_expires(clock: Clock) -> None:
    t = LoginThrottle(max_failures=2, window_s=100)
    fail(t, "a@x.test", 2)
    assert t.locked_for("a@x.test") == 100
    clock.now += 60
    assert t.locked_for("a@x.test") == 40


def test_the_lock_ends_when_the_window_passes(clock: Clock) -> None:
    t = LoginThrottle(max_failures=2, window_s=100)
    fail(t, "a@x.test", 2)
    clock.now += 101
    assert t.locked_for("a@x.test") == 0


def test_failures_are_counted_per_email(clock: Clock) -> None:
    t = LoginThrottle(max_failures=2, window_s=100)
    fail(t, "a@x.test", 2)
    assert t.locked_for("a@x.test") > 0
    assert t.locked_for("b@x.test") == 0


def test_a_successful_login_clears_the_count(clock: Clock) -> None:
    t = LoginThrottle(max_failures=3, window_s=100)
    fail(t, "a@x.test", 2)
    t.clear("a@x.test")
    fail(t, "a@x.test", 2)
    assert t.locked_for("a@x.test") == 0


def test_memory_is_bounded_when_many_different_emails_fail(clock: Clock) -> None:
    t = LoginThrottle(max_failures=5, window_s=100, max_tracked=50)
    for i in range(500):
        t.record_failure(f"user{i}@x.test")
    assert len(t._failures) <= 50
