import pytest

from app.incidents.hints import IncidentFacts, RunFacts, compute_hints


def facts(
    dom: str,
    *,
    status: int | None = None,
    port: int | None = 443,
    detail: str | None = None,
    assertion: str | None = None,
) -> IncidentFacts:
    return IncidentFacts(
        dominant_failure_class=dom,
        latest=RunFacts(7, "FAIL", dom, status, detail),
        run_ids=[5, 6, 7],
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
        ("READ_TIMEOUT", "H_SERVER_STUCK"),
    ],
)
def test_one_rule_per_failure_class(dom: str, expected: str) -> None:
    assert codes(facts(dom)) == [expected]


def test_refused_mentions_the_port() -> None:
    [h] = compute_hints(facts("CONNECTION_REFUSED", port=8080))
    assert "8080" in h.text


def test_every_hint_cites_the_runs_it_is_based_on() -> None:
    for h in compute_hints(facts("DNS_FAILURE")):
        assert h.evidence_run_ids == [5, 6, 7]


def test_5xx_only_for_server_errors() -> None:
    assert codes(facts("HTTP_UNEXPECTED_STATUS", status=503)) == ["H_5XX"]
    assert codes(facts("HTTP_UNEXPECTED_STATUS", status=404)) == []


def test_contract_hint_requires_a_2xx_response() -> None:
    f = facts("ASSERTION_FAILED", status=200, assertion="json_equals expected status=up, got down")
    [h] = compute_hints(f)
    assert h.code == "H_CONTRACT"
    assert "json_equals" in h.text
    assert codes(facts("ASSERTION_FAILED", status=500)) == []


def test_unknown_class_gives_no_hint_rather_than_a_guess() -> None:
    assert codes(facts("PROBE_INTERNAL_ERROR")) == []
