"""Turn a ProbeResult into the column values of a check_runs row (shared by CLI and worker)."""

import dataclasses
import uuid
from datetime import datetime
from typing import Any

from app.probe.types import AttemptResult, Outcome, ProbeResult

HEALTH_BY_OUTCOME = {
    Outcome.PASS: "UP",
    Outcome.DEGRADED: "DEGRADED",
    Outcome.FAIL: "DOWN",
}


def _attempt_json(a: AttemptResult) -> dict[str, Any]:
    d = dataclasses.asdict(a)
    d["started_at"] = a.started_at.isoformat()
    d["error_class"] = a.error_class.value if a.error_class else None
    return d


def build_run_values(
    *,
    monitor_id: uuid.UUID,
    scheduled_for: datetime,
    started_at: datetime,
    finished_at: datetime,
    result: ProbeResult,
    worker_id: str,
    trigger: str,
) -> dict[str, Any]:
    final = result.final
    return {
        "monitor_id": monitor_id,
        "scheduled_for": scheduled_for,
        "started_at": started_at,
        "finished_at": finished_at,
        "outcome": result.outcome.value,
        "failure_class": result.failure_class.value if result.failure_class else None,
        "http_status": final.http_status,
        "dns_ms": final.dns_ms,
        "connect_ms": final.connect_ms,
        "tls_ms": final.tls_ms,
        "ttfb_ms": final.ttfb_ms,
        "total_ms": final.total_ms,
        "resolved_ip": final.resolved_ip,
        "attempt_count": len(result.attempts),
        "attempts": [_attempt_json(a) for a in result.attempts],
        "assertion_results": [dataclasses.asdict(r) for r in result.assertion_results],
        "response_headers": result.response_headers,
        "response_snippet": result.response_snippet,
        "body_truncated": result.body_truncated,
        "error_detail": result.error_detail,
        "worker_id": worker_id,
        "trigger": trigger,
    }
