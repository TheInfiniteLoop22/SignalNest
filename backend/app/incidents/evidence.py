"""Assemble an incident's evidence from stored runs (read-only queries)."""

import uuid
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.incidents.hints import IncidentFacts, RunFacts, compute_hints, hint_to_json


def _facts(row: Any) -> RunFacts:
    return RunFacts(
        run_id=row["id"],
        outcome=row["outcome"],
        failure_class=row["failure_class"],
        http_status=row["http_status"],
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
                    SELECT i.id, i.monitor_id, i.status, i.resolved_at, i.first_failure_run_id,
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

    runs = (
        (
            await session.execute(
                text(
                    """
                    SELECT id, outcome, failure_class, http_status, error_detail,
                           assertion_results
                    FROM check_runs
                    WHERE monitor_id = :m
                      AND id >= :first
                      AND outcome = 'FAIL'
                      AND (CAST(:end AS timestamptz) IS NULL
                           OR started_at <= CAST(:end AS timestamptz))
                    ORDER BY id
                    """
                ),
                {
                    "m": inc["monitor_id"],
                    "first": inc["first_failure_run_id"],
                    "end": inc["resolved_at"],
                },
            )
        )
        .mappings()
        .all()
    )
    class_counts: dict[str, int] = {}
    for r in runs:
        class_counts[r["failure_class"]] = class_counts.get(r["failure_class"], 0) + 1

    hints = []
    first = latest = None
    if runs:
        first, latest = _facts(runs[0]), _facts(runs[-1])
        failed = [a for a in (runs[-1]["assertion_results"] or []) if not a.get("passed", True)]
        summary = "; ".join(
            f"{a['type']} expected {a['expected']}, got {a['actual']}" for a in failed
        )
        hints = compute_hints(
            IncidentFacts(
                dominant_failure_class=inc["dominant_failure_class"],
                latest=latest,
                run_ids=[r["id"] for r in runs][-10:],
                port=urlsplit(inc["url"]).port,
                assertion_summary=summary or None,
            )
        )

    return {
        "incident_id": str(inc["id"]),
        "status": inc["status"],
        "first_failure_run": first.__dict__ if first else None,
        "latest_failure_run": latest.__dict__ if latest else None,
        "class_counts": class_counts,
        "hints": [hint_to_json(h) for h in hints],
    }
