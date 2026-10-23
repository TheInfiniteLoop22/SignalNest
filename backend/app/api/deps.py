"""Shared FastAPI dependencies: current user, DB session, SSRF policy, resolver."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.throttle import LoginThrottle
from app.config import Settings, get_settings
from app.db.models import Session, User
from app.db.session import get_session
from app.probe.engine import SystemResolver
from app.probe.ssrf import SsrfPolicy
from app.probe.types import Resolver
from app.security import token_hash

COOKIE = "sn_session"

DbSession = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def current_user(request: Request, session: DbSession) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "authentication required")
    user = await session.scalar(
        select(User)
        .join(Session, Session.user_id == User.id)
        .where(Session.token_hash == token_hash(token), Session.expires_at > datetime.now(UTC))
    )
    if user is None:
        raise HTTPException(401, "authentication required")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def get_policy(settings: SettingsDep) -> SsrfPolicy:
    return SsrfPolicy.from_settings(settings.ssrf_allow_private_cidrs, settings.allowed_ports)


def get_throttle(request: Request) -> LoginThrottle:
    return request.app.state.login_throttle  # type: ignore[no-any-return]


def get_resolver() -> Resolver:
    return SystemResolver()
