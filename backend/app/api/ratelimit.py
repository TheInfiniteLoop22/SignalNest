"""Fixed-window, in-process rate limiter.

Limitation (stated on purpose): the counters live in this process. With N API replicas the
effective limit is N x the configured one, and a restart resets it. Moving it into Postgres
(or Redis) is the fix once there is more than one replica.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Window:
    start: float
    count: int


class FixedWindowLimiter:
    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._windows: dict[str, _Window] = {}

    def _current(self, key: str) -> _Window:
        now = self._clock()
        w = self._windows.get(key)
        if w is None or now - w.start >= self.window_s:
            w = _Window(now, 0)
            self._windows[key] = w
        return w

    def retry_after(self, key: str) -> int:
        w = self._current(key)
        return max(1, int(w.start + self.window_s - self._clock()) + 1)

    def blocked(self, key: str) -> bool:
        """True when the key has used up its allowance (does not count a hit)."""
        return self._current(key).count >= self.limit

    def hit(self, key: str) -> bool:
        """Count one event; return False when it exceeds the limit."""
        w = self._current(key)
        w.count += 1
        return w.count <= self.limit

    def reset(self, key: str) -> None:
        self._windows.pop(key, None)
