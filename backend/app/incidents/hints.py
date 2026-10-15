"""Diagnosis hints: a few deterministic rules over stored evidence. No ML, no baselines."""

from dataclasses import dataclass, field
from typing import Any

from app.probe.types import FailureClass

F = FailureClass


@dataclass(frozen=True)
class RunFacts:
    run_id: int
    outcome: str
    failure_class: str | None
    http_status: int | None
    error_detail: str | None = None


@dataclass(frozen=True)
class Hint:
    code: str
    text: str
    evidence_run_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class IncidentFacts:
    dominant_failure_class: str
    latest: RunFacts
    run_ids: list[int]
    port: int | None = None
    assertion_summary: str | None = None


def compute_hints(facts: IncidentFacts) -> list[Hint]:
    """At most one hint: the rule matching the incident's most frequent failure class."""
    dom = facts.dominant_failure_class
    latest = facts.latest
    ids = facts.run_ids

    if dom in (F.DNS_FAILURE, F.DNS_TIMEOUT):
        return [
            Hint(
                "H_DNS",
                "Name resolution is failing: check the domain's DNS records or registration.",
                ids,
            )
        ]
    if dom == F.CONNECTION_REFUSED:
        port = facts.port if facts.port is not None else "the target"
        return [
            Hint(
                "H_REFUSED",
                f"The host is reachable but nothing accepts connections on port {port}. "
                "The service may be stopped or listening on another port.",
                ids,
            )
        ]
    if dom == F.CONNECT_TIMEOUT:
        return [
            Hint(
                "H_NET_DROP",
                "Connection attempts get no reply: typical of a firewall dropping packets "
                "or a host that is down.",
                ids,
            )
        ]
    if dom == F.TLS_CERT_INVALID:
        reason = latest.error_detail or "verification failed"
        return [
            Hint(
                "H_TLS_CERT",
                f"The certificate failed verification ({reason}): check expiry and hostname.",
                ids,
            )
        ]
    if dom == F.READ_TIMEOUT:
        return [
            Hint(
                "H_SERVER_STUCK",
                "The connection succeeded but the server never sent response headers in "
                "time: the application is overloaded or stuck.",
                ids,
            )
        ]
    if dom == F.HTTP_UNEXPECTED_STATUS and (latest.http_status or 0) >= 500:
        return [
            Hint(
                "H_5XX",
                f"The server is responding with {latest.http_status} errors: the application "
                "or an upstream dependency is failing.",
                ids,
            )
        ]
    if (
        dom == F.ASSERTION_FAILED
        and latest.http_status is not None
        and 200 <= latest.http_status < 300
    ):
        what = facts.assertion_summary or "an assertion"
        return [
            Hint(
                "H_CONTRACT",
                f"The endpoint is up but its response no longer matches expectations ({what}).",
                ids,
            )
        ]
    return []


def hint_to_json(h: Hint) -> dict[str, Any]:
    return {"code": h.code, "text": h.text, "evidence_run_ids": h.evidence_run_ids}
