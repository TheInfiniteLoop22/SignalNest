from datetime import UTC, datetime, timedelta

import pytest

from app.worker.schedule import compute_next_run_at

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.mark.parametrize(
    ("scheduled", "interval", "now", "jitter", "expected"),
    [
        # on time: next slot is exactly scheduled + interval (no drift)
        (at(0), 30, at(1), 2.0, at(30)),
        # finished late but still before the next slot: unchanged
        (at(0), 30, at(29.9), 2.0, at(30)),
        # exactly at the next slot is not "missed"
        (at(0), 30, at(30), 2.0, at(30)),
        # missed: skip to now + jitter, no backfill
        (at(0), 30, at(31), 2.0, at(33)),
        # 10 minutes of downtime on a 30 s monitor -> one prompt run, not 20 catch-ups
        (at(0), 30, at(600), 0.0, at(600)),
        (at(0), 30, at(600), 3.5, at(603.5)),
    ],
)
def test_compute_next_run_at(
    scheduled: datetime, interval: int, now: datetime, jitter: float, expected: datetime
) -> None:
    assert compute_next_run_at(scheduled, interval, now, jitter) == expected


def test_result_is_never_in_the_past_relative_to_now() -> None:
    for late in (1, 31, 3600):
        assert compute_next_run_at(at(0), 30, at(late), 0.0) >= at(late)
