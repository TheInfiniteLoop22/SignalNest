import pytest

from app.incidents.hints import (
    Baseline,
    IncidentFacts,
    RunFacts,
    compute_hints,
)

GOOD = Baseline(median_connect_ms=20.0, median_ttfb_ms=50.0, sample_count=100)
NONE = Baseline(None, None, 3)


def facts(
    dom: str,
    *,
    outcome: str = "FAIL",
    status: int | None = None,
    connect: int | None = 20,
    ttfb: int | None = None,
    base: Baseline = GOOD,
    port: int | None = 443,
    detail: str | None = None,
    assertion: str | None = None,
) -> IncidentFacts:
    return IncidentFacts(
        dominant_failure_class=dom,
        latest=RunFacts(7, outcome, dom, status, connect, ttfb, detail),
        run_ids=[5, 6, 7],
        baseline=base,
        port=port,
        assertion_summary=assertion,
    )


def codes(f: IncidentFacts) -> list[str]:
    return [h.code for h in compute_hints(f)]


@pytest.mark.parametrize(
    ("dom", "expected"),
    [
        ("DNS_FAILURE", "H_DNS"),
        ("DNS_TIMEOUT", "H_DNS"),
        ("CONNECTION_REFUSED", "H_REFUSED"),
        ("CONNECT_TIMEOUT", "H_NET_DROP"),
        ("TLS_CERT_INVALID", "H_TLS_CERT"),
    ],
)
def test_simple_class_rules(dom: str, expected: str) -> None:
    assert codes(facts(dom)) == [expected]


def test_refused_mentions_the_port() -> None:
    [h] = compute_hints(facts("CONNECTION_REFUSED", port=8080))
    assert "8080" in h.text


def test_every_hint_cites_run_ids() -> None:
    for h in compute_hints(facts("DNS_FAILURE")):
        assert h.evidence_run_ids == [5, 6, 7]


def test_5xx_only_for_server_errors() -> None:
    assert codes(facts("HTTP_UNEXPECTED_STATUS", status=503)) == ["H_5XX"]
    assert codes(facts("HTTP_UNEXPECTED_STATUS", status=404)) == []


def test_contract_hint_requires_2xx() -> None:
    f = facts("ASSERTION_FAILED", status=200, assertion="json_equals expected status=up, got down")
    [h] = compute_hints(f)
    assert h.code == "H_CONTRACT"
    assert "json_equals" in h.text
    assert codes(facts("ASSERTION_FAILED", status=500)) == []


def test_server_slow_when_connect_normal_and_ttfb_high() -> None:
    f = facts("READ_TIMEOUT", connect=25, ttfb=400)  # 1.25x connect, 8x ttfb
    [h] = compute_hints(f)
    assert h.code == "H_SERVER_SLOW"
    assert "8x" in h.text


def test_net_slow_when_connect_is_3x_baseline() -> None:
    assert codes(facts("READ_TIMEOUT", connect=90, ttfb=500)) == ["H_NET_SLOW"]


def test_degraded_run_gets_timing_hint_too() -> None:
    f = facts("", outcome="DEGRADED", connect=20, ttfb=300)
    assert codes(f) == ["H_SERVER_SLOW"]


def test_no_baseline_is_stated_not_guessed() -> None:
    assert codes(facts("READ_TIMEOUT", base=NONE)) == ["H_NO_BASELINE"]


def test_read_timeout_without_ttfb_and_normal_connect_blames_server() -> None:
    assert codes(facts("READ_TIMEOUT", connect=18, ttfb=None)) == ["H_SERVER_SLOW"]


def test_unknown_class_has_no_hint() -> None:
    assert codes(facts("PROBE_INTERNAL_ERROR")) == []
