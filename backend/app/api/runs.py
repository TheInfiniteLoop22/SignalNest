import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Monitor, User
from app.db.session import get_session
from app.services.probe_once import list_runs

router = APIRouter(prefix="/api/v1")


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime
    outcome: str
    failure_class: str | None
    http_status: int | None
    total_ms: int | None
    error_detail: str | None
    trigger: str


async def current_user(session: Annotated[AsyncSession, Depends(get_session)]) -> User:
    """Pre-auth stand-in (replaced in M6): everything acts as the seeded dev user."""
    user = await session.scalar(select(User).where(User.email == get_settings().dev_user_email))
    if user is None:
        raise HTTPException(status_code=401, detail="no dev user; run `python -m app.cli seed`")
    return user


@router.get("/monitors/{monitor_id}/runs", response_model=list[RunOut])
async def get_runs(
    monitor_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunOut]:
    monitor = await session.scalar(
        select(Monitor).where(Monitor.id == monitor_id, Monitor.owner_id == user.id)
    )
    if monitor is None:  # 404, not 403: don't reveal that other users' ids exist
        raise HTTPException(status_code=404, detail="monitor not found")
    runs = await list_runs(session, monitor_id, limit=limit)
    return [RunOut.model_validate(r) for r in runs]
