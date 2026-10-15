import json
import logging

import pytest

from app.observability import metrics
from app.observability.logging import JsonFormatter, redact_url, request_id_var


def format_record(**extra: object) -> dict[str, object]:
    rec = logging.LogRecord(
        "signalnest.test", logging.INFO, __file__, 1, "run.completed", None, None
    )
    for k, v in extra.items():
        setattr(rec, k, v)
    return json.loads(JsonFormatter("worker").format(rec))  # type: ignore[no-any-return]


def test_log_line_is_one_json_object_with_context() -> None:
    out = format_record(
        monitor_id="m1", outcome="FAIL", failure_class="READ_TIMEOUT", duration_ms=5003
    )
    assert out["service"] == "worker"
    assert out["level"] == "INFO"
    assert out["msg"] == "run.completed"
    assert out["monitor_id"] == "m1"
    assert out["duration_ms"] == 5003
    assert out["ts"].endswith("+00:00")  # type: ignore[union-attr]


def test_request_id_is_attached_from_context() -> None:
    token = request_id_var.set("req-123")
    try:
        assert format_record()["request_id"] == "req-123"
    finally:
        request_id_var.reset(token)
    assert "request_id" not in format_record()


def test_exceptions_are_included_as_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "x", None, sys.exc_info())
    assert "ValueError: boom" in json.loads(JsonFormatter("api").format(rec))["error"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://api.example.com/v1/x?token=abc&user=bob",
            "https://api.example.com/v1/x?token=***&user=***",
        ),
        ("https://u:p@api.example.com:8443/p", "https://api.example.com:8443/p"),
        ("http://h/p#frag", "http://h/p"),
        ("https://h/p?empty=", "https://h/p?empty=***"),
    ],
)
def test_redact_url(raw: str, expected: str) -> None:
    out = redact_url(raw)
    assert out == expected
    assert "abc" not in out and "bob" not in out and "u:p" not in out


FORBIDDEN_LABELS = {"monitor_id", "url", "user", "user_id", "run_id", "email", "path", "ip"}


def test_no_metric_uses_an_unbounded_label() -> None:
    """Cardinality guard: ids/URLs as labels would create a time series per value."""
    families = list(metrics.registry.collect())
    assert families, "metrics registry is empty"
    for fam in families:
        for sample in fam.samples:
            assert not (set(sample.labels) & FORBIDDEN_LABELS), (fam.name, sample.labels)
    declared = {
        "signalnest_runs_total": {"outcome", "failure_class"},
        "signalnest_http_requests_total": {"route", "method", "status"},
    }
    for name, labels in declared.items():
        metric = next(
            m
            for m in metrics.registry._names_to_collectors.values()  # noqa: SLF001
            if getattr(m, "_name", "") in (name, name.removesuffix("_total"))
        )
        assert set(metric._labelnames) == labels  # noqa: SLF001
