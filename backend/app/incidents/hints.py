"""Deterministic diagnosis hints (docs/07 section 8). Pure rules over stored evidence; no ML."""

from dataclasses import dataclass, field
from typing import Any

from app.probe.types import FailureClass

MIN_BASELINE_SAMPLES = 20


@dataclass(frozen=True)
class Baseline:
    """Medians over recent PASS runs for this monitor."""

    median_connect_ms: float | None
    median_ttfb_ms: float | None
    sample_count: int

    @property
    def usable(self) -> bool:
        return self.sample_count >= MIN_BASELINE_SAMPLES


@dataclass(frozen=True)
class RunFacts:
    run_id: int
    outcome: str
    failure_class: str | None
    http_status: int | None
    connect_ms: int | None
    ttfb_ms: int | None
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
    baseline: Baseline
    port: int | None = None
    assertion_summary: str | None = None


F = FailureClass


def _ratio(value: int | None, base: float | None) -> float | None:
    if value is None or base is None or base <= 0:
        return None
    return value / base


def compute_hints(facts: IncidentFacts) -> list[Hint]:
    dom = facts.dominant_failure_class
    latest = facts.latest
    ids = facts.run_ids
    hints: list[Hint] = []

    if dom in (F.DNS_FAILURE, F.DNS_TIMEOUT):
        hints.append(
            Hint(
                "H_DNS",
                "Name resolution is failing — check the domain's DNS records or registration.",
                ids,
            )
        )
    elif dom == F.CONNECTION_REFUSED:
        port = facts.port if facts.port is not None else "the target"
        hints.append(
            Hint(
                "H_REFUSED",
                f"The host is reachable but nothing accepts connections on port {port}. "
                "The service may be stopped or listening on another port.",
                ids,
            )
        )
    elif dom == F.CONNECT_TIMEOUT:
        hints.append(
            Hint(
                "H_NET_DROP",
                "Connection attempts get no reply — typical of a firewall dropping packets "
                "or a host that is down.",
                ids,
            )
        )
    elif dom == F.TLS_CERT_INVALID:
        reason = latest.error_detail or "verification failed"
        hints.append(
            Hint(
                "H_TLS_CERT",
                f"The certificate failed verification ({reason}) — check expiry and hostname.",
                ids,
            )
        )
    elif dom == F.HTTP_UNEXPECTED_STATUS and (latest.http_status or 0) >= 500:
        hints.append(
            Hint(
                "H_5XX",
                f"The server is responding with {latest.http_status} errors — the application "
                "or an upstream dependency is failing.",
                ids,
            )
        )
    elif (
        dom == F.ASSERTION_FAILED
        and latest.http_status is not None
        and 200 <= latest.http_status < 300
    ):
        what = facts.assertion_summary or "an assertion"
        hints.append(
            Hint(
                "H_CONTRACT",
                f"The endpoint is up but its response no longer matches expectations ({what}).",
                ids,
            )
        )

    # Timing hints compare against this monitor's own recent healthy runs. Without enough of
    # them we say so instead of guessing.
    is_timing_failure = dom in (F.READ_TIMEOUT, F.TOTAL_TIMEOUT) or latest.outcome == "DEGRADED"
    if is_timing_failure:
        base = facts.baseline
        if not base.usable:
            hints.append(
                Hint(
                    "H_NO_BASELINE",
                    f"Not enough history for a baseline comparison "
                    f"({base.sample_count} of {MIN_BASELINE_SAMPLES} passing runs).",
                    ids,
                )
            )
        else:
            conn_ratio = _ratio(latest.connect_ms, base.median_connect_ms)
            ttfb_ratio = _ratio(latest.ttfb_ms, base.median_ttfb_ms)
            connect_ok = conn_ratio is None or conn_ratio <= 2
            if conn_ratio is not None and conn_ratio >= 3:
                hints.append(
                    Hint(
                        "H_NET_SLOW",
                        f"Connection setup is {conn_ratio:.0f}x slower than usual — likely "
                        "network path or load-balancer trouble.",
                        ids,
                    )
                )
            elif connect_ok and ttfb_ratio is not None and ttfb_ratio >= 3:
                hints.append(
                    Hint(
                        "H_SERVER_SLOW",
                        f"Network setup is normal ({latest.connect_ms} ms vs "
                        f"{base.median_connect_ms:.0f} ms baseline) but time-to-first-byte is "
                        f"{ttfb_ratio:.0f}x baseline — time is being spent inside the server.",
                        ids,
                    )
                )
            elif connect_ok and latest.ttfb_ms is None:
                hints.append(
                    Hint(
                        "H_SERVER_SLOW",
                        "Connection setup is normal but the server never produced response "
                        "headers within the timeout — time is being spent inside the server.",
                        ids,
                    )
                )
    return hints


def hint_to_json(h: Hint) -> dict[str, Any]:
    return {"code": h.code, "text": h.text, "evidence_run_ids": h.evidence_run_ids}
