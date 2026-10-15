from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.api.deps import CurrentUser, DbSession, require_csrf_header
from app.api.errors import Problem
from app.api.schemas import RunDetail, Timings
from app.services.explain import explain

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_csrf_header)])


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: int, session: DbSession, user: CurrentUser) -> RunDetail:
    # Ownership is enforced by joining through the monitor; another user's run is a 404.
    row = (
        (
            await session.execute(
                text(
                    """
                    SELECT r.*, CAST(r.resolved_ip AS text) AS resolved_ip_text
                    FROM check_runs r
                    JOIN monitors m ON m.id = r.monitor_id
                    WHERE r.id = :id AND m.owner_id = :owner
                    """
                ),
                {"id": run_id, "owner": user.id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "not-found", "run not found")
    return RunDetail(
        id=row["id"],
        monitor_id=row["monitor_id"],
        scheduled_for=row["scheduled_for"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        outcome=row["outcome"],
        failure_class=row["failure_class"],
        explanation=explain(row["failure_class"], row["outcome"]),
        timings=Timings(
            dns_ms=row["dns_ms"],
            connect_ms=row["connect_ms"],
            tls_ms=row["tls_ms"],
            ttfb_ms=row["ttfb_ms"],
            total_ms=row["total_ms"],
        ),
        resolved_ip=row["resolved_ip_text"],
        attempt_count=row["attempt_count"],
        attempts=row["attempts"],
        http_status=row["http_status"],
        assertion_results=row["assertion_results"],
        response_headers=row["response_headers"],
        response_snippet=row["response_snippet"],
        body_truncated=row["body_truncated"],
        error_detail=row["error_detail"],
        trigger=row["trigger"],
        worker_id=row["worker_id"],
    )


__all__ = ["Annotated", "router"]
