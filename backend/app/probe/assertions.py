"""Assertion evaluation. Pure functions; no JSONPath library on purpose."""

import json
from collections.abc import Sequence
from typing import Any

from app.probe.types import (
    Assertion,
    AssertionResult,
    BodyContains,
    JsonEquals,
    LatencyBelow,
    StatusIn,
)

_MISSING = object()


def walk_path(data: Any, path: str) -> Any:
    """Follow a dotted path (`items.0.id`). Returns _MISSING when any step is absent.

    A segment indexes a list when the current node is a list and the segment is a
    non-negative integer; otherwise it is a dict key. No wildcards, no negative indexes.
    """
    cur = data
    for seg in path.split("."):
        if isinstance(cur, list):
            if not seg.isdigit() or int(seg) >= len(cur):
                return _MISSING
            cur = cur[int(seg)]
        elif isinstance(cur, dict):
            if seg not in cur:
                return _MISSING
            cur = cur[seg]
        else:
            return _MISSING
    return cur


def scalar_equal(actual: Any, expected: Any) -> bool:
    """Type-aware equality: True != 1, "1" != 1, but 1 == 1.0."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return isinstance(actual, bool) and isinstance(expected, bool) and actual == expected
    if isinstance(actual, int | float) and isinstance(expected, int | float):
        return actual == expected
    return type(actual) is type(expected) and actual == expected


def default_status_allowed(status: int | None) -> bool:
    return status is not None and 200 <= status <= 399


def _status(a: StatusIn, status: int | None) -> AssertionResult:
    if a.values is not None:
        expected: Any = a.values
        ok = status in a.values
    else:
        assert a.range_ is not None
        lo, hi = a.range_
        expected = [lo, hi]
        ok = status is not None and lo <= status <= hi
    return AssertionResult("status_in", expected, status, ok, "" if ok else "status not allowed")


def _latency(a: LatencyBelow, total_ms: int | None) -> AssertionResult:
    ok = total_ms is not None and total_ms < a.ms
    return AssertionResult(
        "latency_below", f"< {a.ms} ms", total_ms, ok, "" if ok else "response too slow"
    )


def _body(a: BodyContains, body: bytes) -> AssertionResult:
    text = body.decode("utf-8", errors="replace")
    ok = a.text in text
    return AssertionResult(
        "body_contains", a.text, "<found>" if ok else "<not found>", ok, "" if ok else "text absent"
    )


def _json(a: JsonEquals, body: bytes) -> AssertionResult:
    expected = {a.path: a.value}
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return AssertionResult("json_equals", expected, "<not JSON>", False, "body is not JSON")
    found = walk_path(data, a.path)
    if found is _MISSING:
        return AssertionResult("json_equals", expected, "<missing>", False, "path not found")
    ok = scalar_equal(found, a.value)
    return AssertionResult("json_equals", expected, found, ok, "" if ok else "value differs")


def evaluate(
    assertions: Sequence[Assertion],
    *,
    http_status: int | None,
    total_ms: int | None,
    body: bytes,
) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    for a in assertions:
        if isinstance(a, StatusIn):
            results.append(_status(a, http_status))
        elif isinstance(a, LatencyBelow):
            results.append(_latency(a, total_ms))
        elif isinstance(a, BodyContains):
            results.append(_body(a, body))
        elif isinstance(a, JsonEquals):
            results.append(_json(a, body))
    return results
