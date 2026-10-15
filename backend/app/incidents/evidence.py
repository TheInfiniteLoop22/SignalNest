"""Assemble an incident's evidence from stored runs (read-only queries).

The API (M6) returns this verbatim: first/latest failing run, class counts, baseline medians
and rule-based hints, each hint carrying the run ids it was derived from.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.incidents.hints import (
    Baseline,
    IncidentFacts,
    RunFacts,
    compute_hints,
    hint_to_json,
)

BASELINE_WINDOW = timedelta(days=7)


async def baseline_for(
    session: AsyncSession, monitor_id: uuid.UUID, *, before: datetime
) -> Baseline:
    """Medians of connect/ttfb over the 7 days of PASS runs preceding `before`.

    Medians, not means: one slow outlier should not move the baseline.
    """
    row = (
        (
            await session.execute(
                text(
                    """
                    SELECT count(*) AS n,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY connect_ms) AS connect,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY ttfb_ms) AS ttfb
                    FROM check_runs
                    WHERE monitor_id = :m
                      AND outcome = 'PASS'
                      AND started_at < :before
                      AND started_at >= :since
                    """
                ),
                {"m": monitor_id, "before": before, "since": before - BASELINE_WINDOW},
            )
        )
        .mappings()
        .one()
    )
    return Baseline(
        median_connect_ms=float(row["connect"]) if row["connect"] is not None else None,
        median_ttfb_ms=float(row["ttfb"]) if row["ttfb"] is not None else None,
        sample_count=int(row["n"]),
    )


def _facts(row: Any) -> RunFacts:
    return RunFacts(
        run_id=row["id"],
        outcome=row["outcome"],
        failure_class=row["failure_class"],
        http_status=row["http_status"],
        connect_ms=row["connect_ms"],
        ttfb_ms=row["ttfb_ms"],
        error_detail=row["error_detail"],
    )


async def build_incident_evidence(
    session: AsyncSession, incident_id: uuid.UUID
) -> dict[str, Any] | None:
    inc = (
        (
            await session.execute(
                text(
                    """
                    SELECT i.id, i.monitor_id, i.status, i.opened_at, i.resolved_at,
                           i.first_failure_run_id, i.resolving_run_id, i.failure_count,
                           i.dominant_failure_class, m.url
                    FROM incidents i JOIN monitors m ON m.id = i.monitor_id
                    WHERE i.id = :id
                    """
                ),
                {"id": incident_id},
            )
        )
        .mappings()
        .first()
    )
    if inc is None:
        return None

    end = inc["resolved_at"]
    runs = (
        (
            await session.execute(
                text(
                    """
                    SELECT id, outcome, failure_class, http_status, connect_ms, ttfb_ms,
                           error_detail, assertion_results
                    FROM check_runs
                    WHERE monitor_id = :m
                      AND id >= :first
                      AND outcome = 'FAIL'
                      AND (CAST(:end AS timestamptz) IS NULL
                           OR started_at <= CAST(:end AS timestamptz))
                    ORDER BY id
                    """
                ),
                {"m": inc["monitor_id"], "first": inc["first_failure_run_id"], "end": end},
            )
        )
        .mappings()
        .all()
    )
    class_counts: dict[str, int] = {}
    for r in runs:
        class_counts[r["failure_class"]] = class_counts.get(r["failure_class"], 0) + 1

    base = await baseline_for(session, inc["monitor_id"], before=inc["opened_at"])
    hints = []
    first = latest = None
    if runs:
        first, latest = _facts(runs[0]), _facts(runs[-1])
        failed = [a for a in (runs[-1]["assertion_results"] or []) if not a.get("passed", True)]
        summary = "; ".join(
            f"{a['type']} expected {a['expected']}, got {a['actual']}" for a in failed
        )
        port = urlsplit(inc["url"]).port
        hints = compute_hints(
            IncidentFacts(
                dominant_failure_class=inc["dominant_failure_class"],
                latest=latest,
                run_ids=[r["id"] for r in runs][-10:],
                baseline=base,
                port=port,
                assertion_summary=summary or None,
            )
        )

    return {
        "incident_id": str(inc["id"]),
        "status": inc["status"],
        "first_failure_run": first.__dict__ if first else None,
        "latest_failure_run": latest.__dict__ if latest else None,
        "class_counts": class_counts,
        "baseline": {
            "median_connect_ms": base.median_connect_ms,
            "median_ttfb_ms": base.median_ttfb_ms,
            "sample_count": base.sample_count,
        },
        "hints": [hint_to_json(h) for h in hints],
    }
