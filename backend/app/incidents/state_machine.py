"""Incident state machine. Pure: no I/O, no clock, no database.

A monitor opens an incident after `failure_threshold` consecutive failed probes and resolves it
on the first pass. Waiting for several failures in a row keeps one dropped packet from paging
anyone; the price is detecting a real outage `threshold - 1` probes later.
"""

from dataclasses import dataclass
from enum import StrEnum


class Action(StrEnum):
    NONE = "NONE"
    OPEN = "OPEN"  # create an incident
    RESOLVE = "RESOLVE"  # close the open incident


@dataclass(frozen=True)
class Decision:
    status: str  # UNKNOWN | UP | DOWN
    consecutive_failures: int
    action: Action


def evaluate(
    *, status: str, consecutive_failures: int, passed: bool, failure_threshold: int
) -> Decision:
    """`status` is the monitor's current status; DOWN means an incident is open."""
    if passed:
        action = Action.RESOLVE if status == "DOWN" else Action.NONE
        return Decision("UP", 0, action)

    failures = consecutive_failures + 1
    if status != "DOWN" and failures >= failure_threshold:
        return Decision("DOWN", failures, Action.OPEN)
    return Decision(status, failures, Action.NONE)
