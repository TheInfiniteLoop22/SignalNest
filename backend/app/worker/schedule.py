"""Scheduling arithmetic (ADR-005). Pure; no clock, no DB."""

from datetime import datetime, timedelta


def compute_next_run_at(
    scheduled_for: datetime,
    interval_s: int,
    now: datetime,
    jitter_s: float = 0.0,
) -> datetime:
    """Fixed-rate scheduling without backfill.

    The next slot is `scheduled_for + interval`, so completion latency never causes drift.
    If that slot is already in the past (the worker was down or slow), the missed slots are
    skipped: the next run is `now + jitter`, a single prompt run rather than a catch-up storm.
    """
    nxt = scheduled_for + timedelta(seconds=interval_s)
    if nxt < now:
        return now + timedelta(seconds=jitter_s)
    return nxt
