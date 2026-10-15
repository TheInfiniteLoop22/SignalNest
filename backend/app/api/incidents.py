import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.schemas import IncidentOut
from app.db.models import Incident, Monitor

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=list[IncidentOut])
async def list_incidents(
    session: DbSession,
    user: CurrentUser,
    status: Literal["OPEN", "RESOLVED"] | None = None,
    monitor_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[IncidentOut]:
    # Ownership comes from the join: another user's incidents are simply not in the result.
    stmt = (
        select(Incident, Monitor.name)
        .join(Monitor, Monitor.id == Incident.monitor_id)
        .where(Monitor.owner_id == user.id)
        .order_by(Incident.resolved_at.is_(None).desc(), Incident.opened_at.desc())
        .limit(limit)
    )
    if status == "OPEN":
        stmt = stmt.where(Incident.resolved_at.is_(None))
    elif status == "RESOLVED":
        stmt = stmt.where(Incident.resolved_at.is_not(None))
    if monitor_id:
        stmt = stmt.where(Incident.monitor_id == monitor_id)

    rows = await session.execute(stmt)
    return [
        IncidentOut(
            id=i.id,
            monitor_id=i.monitor_id,
            monitor_name=name,
            status="OPEN" if i.resolved_at is None else "RESOLVED",
            opened_at=i.opened_at,
            resolved_at=i.resolved_at,
            failure_class=i.failure_class,
        )
        for i, name in rows
    ]
