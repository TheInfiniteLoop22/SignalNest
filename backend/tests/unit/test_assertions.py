import json
from typing import Any

import pytest

from app.probe.assertions import _MISSING, evaluate, scalar_equal, walk_path
from app.probe.types import (
    Assertion,
    AssertionResult,
    BodyContains,
    JsonEquals,
    LatencyBelow,
    StatusIn,
)


def run(a: Assertion, *, status: int = 200, ms: int = 50, body: bytes = b"") -> AssertionResult:
    return evaluate([a], http_status=status, total_ms=ms, body=body)[0]


@pytest.mark.parametrize(
    ("data", "path", "expected"),
    [
        ({"a": {"b": 1}}, "a.b", 1),
        ({"items": [{"id": 7}]}, "items.0.id", 7),
        ({"items": []}, "items.0.id", _MISSING),
        ({"items": [1]}, "items.-1", _MISSING),
        ({"a": 1}, "a.b", _MISSING),
        ({"a": None}, "a", None),
        ([10, 20], "1", 20),
    ],
)
def test_walk_path(data: Any, path: str, expected: Any) -> None:
    assert walk_path(data, path) is expected or walk_path(data, path) == expected


@pytest.mark.parametrize(
    ("a", "b", "eq"),
    [(1, 1.0, True), (True, 1, False), ("1", 1, False), (None, None, True), ("a", "a", True)],
)
def test_scalar_equal(a: Any, b: Any, eq: bool) -> None:
    assert scalar_equal(a, b) is eq


def test_status_values_and_range() -> None:
    assert run(StatusIn(values=[200, 204])).passed
    assert not run(StatusIn(values=[204])).passed
    assert run(StatusIn(range=(200, 299)), status=250).passed
    assert not run(StatusIn(range=(200, 299)), status=301).passed


def test_status_in_requires_exactly_one_form() -> None:
    with pytest.raises(ValueError):
        StatusIn()
    with pytest.raises(ValueError):
        StatusIn(values=[200], range=(1, 2))


def test_latency_below_is_strict() -> None:
    assert run(LatencyBelow(ms=100), ms=99).passed
    assert not run(LatencyBelow(ms=100), ms=100).passed


def test_body_contains_is_case_sensitive() -> None:
    assert run(BodyContains(text="ok"), body=b"all ok").passed
    assert not run(BodyContains(text="OK"), body=b"all ok").passed


def test_json_equals_ac10_shape() -> None:
    r = run(JsonEquals(path="status", value="up"), body=json.dumps({"status": "down"}).encode())
    assert not r.passed
    assert r.actual == "down"


def test_json_not_json_and_missing() -> None:
    assert run(JsonEquals(path="a", value=1), body=b"<html>").actual == "<not JSON>"
    assert run(JsonEquals(path="a", value=1), body=b"{}").actual == "<missing>"
