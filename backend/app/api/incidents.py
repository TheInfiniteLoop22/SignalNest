import base64
import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.api.deps import CurrentUser, DbSession
from app.api.errors import Problem
from app.api.schemas import IncidentDetail, IncidentEventOut, IncidentOut, IncidentPage
from app.incidents.evidence import build_incident_evidence

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _encode(rank: int, opened_at: datetime, incident_id: uuid.UUID) -> str:
    raw = json.dumps([rank, opened_at.isoformat(), str(incident_id)], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode(cursor: str) -> tuple[int, datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        rank, ts, iid = json.loads(raw)
        return int(rank), datetime.fromisoformat(ts), uuid.UUID(iid)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise Problem(422, "validation", "invalid cursor") from exc


@router.get("", response_model=IncidentPage)
async def list_incidents(
    session: DbSession,
    user: CurrentUser,
    status: Literal["OPEN", "RESOLVED"] | None = None,
    monitor_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> IncidentPage:
    where = ["m.owner_id = :owner"]
    params: dict[str, Any] = {"owner": user.id, "limit": limit + 1}
    if status:
        where.append("i.status = :status")
        params["status"] = status
    if monitor_id:
        where.append("i.monitor_id = :monitor")
        params["monitor"] = monitor_id
    if cursor:
        rank, ts, iid = _decode(cursor)
        where.append(
            "((i.status = 'OPEN')::int, i.opened_at, i.id) < (:c_rank, :c_ts, CAST(:c_id AS uuid))"
        )
        params.update(c_rank=rank, c_ts=ts, c_id=str(iid))
    rows = (
        (
            await session.execute(
                text(
                    f"""
                    SELECT i.id, i.monitor_id, i.status, i.opened_at, i.resolved_at,
                           i.failure_count, i.dominant_failure_class
                    FROM incidents i JOIN monitors m ON m.id = i.monitor_id
                    WHERE {" AND ".join(where)}
                    ORDER BY (i.status = 'OPEN')::int DESC, i.opened_at DESC, i.id DESC
                    LIMIT :limit
                    """  # noqa: S608 - fixed fragments only; values are bound
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = _encode(1 if last["status"] == "OPEN" else 0, last["opened_at"], last["id"])
    return IncidentPage(items=[IncidentOut(**dict(r)) for r in page], next_cursor=next_cursor)


@router.get("/{incident_id}", response_model=IncidentDetail)
async def get_incident(
    incident_id: uuid.UUID, session: DbSession, user: CurrentUser
) -> IncidentDetail:
    row = (
        (
            await session.execute(
                text(
                    """
                    SELECT i.* FROM incidents i JOIN monitors m ON m.id = i.monitor_id
                    WHERE i.id = :id AND m.owner_id = :owner
                    """
                ),
                {"id": incident_id, "owner": user.id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise Problem(404, "not-found", "incident not found")
    events = (
        (
            await session.execute(
                text(
                    "SELECT at, kind, run_id, detail FROM incident_events "
                    "WHERE incident_id = :id ORDER BY id"
                ),
                {"id": incident_id},
            )
        )
        .mappings()
        .all()
    )
    evidence = await build_incident_evidence(session, incident_id)
    return IncidentDetail(
        id=row["id"],
        monitor_id=row["monitor_id"],
        status=row["status"],
        opened_at=row["opened_at"],
        resolved_at=row["resolved_at"],
        failure_count=row["failure_count"],
        dominant_failure_class=row["dominant_failure_class"],
        first_failure_run_id=row["first_failure_run_id"],
        resolving_run_id=row["resolving_run_id"],
        events=[IncidentEventOut(**dict(e)) for e in events],
        evidence=evidence or {},
    )
