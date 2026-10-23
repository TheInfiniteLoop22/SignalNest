"""Login throttle: too many failed logins for one email locks that email for a while.

Kept in memory, which is correct for this app because the API runs as a single process. (With
several API processes each would keep its own count; the counters would then belong in the
database.) Unknown emails are throttled exactly like known ones, so the response never reveals
which accounts exist. While an email is locked, further attempts are refused before the
password is checked and are not counted, so the lock cannot be extended forever.
"""

import math
import time
from collections import deque


class LoginThrottle:
    def __init__(self, max_failures: int, window_s: int, max_tracked: int = 10_000) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.max_tracked = max_tracked
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, email: str, now: float) -> deque[float]:
        """The failures for `email` that still count, oldest first."""
        times = self._failures.get(email)
        if times is None:
            return deque()
        while times and now - times[0] >= self.window_s:
            times.popleft()
        if not times:
            del self._failures[email]
        return times

    def locked_for(self, email: str) -> int:
        """Seconds until `email` may try again, or 0 if it is not locked."""
        now = time.monotonic()
        times = self._recent(email, now)
        if len(times) < self.max_failures:
            return 0
        return max(1, math.ceil(times[0] + self.window_s - now))

    def record_failure(self, email: str) -> None:
        now = time.monotonic()
        self._recent(email, now)  # drop failures that no longer count
        self._failures.setdefault(email, deque()).append(now)
        self._cap(now)

    def clear(self, email: str) -> None:
        self._failures.pop(email, None)

    def _cap(self, now: float) -> None:
        """Bound memory: someone could try millions of different emails."""
        if len(self._failures) <= self.max_tracked:
            return
        for email in list(self._failures):
            self._recent(email, now)
        while len(self._failures) > self.max_tracked:
            del self._failures[next(iter(self._failures))]  # drop the oldest-tracked email
