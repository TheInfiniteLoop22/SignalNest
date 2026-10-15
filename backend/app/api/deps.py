"""Shared FastAPI dependencies: current user, DB session, SSRF policy, resolver."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Problem
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
        raise Problem(401, "unauthenticated", "authentication required")
    now = datetime.now(UTC)
    row = (
        await session.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(
                Session.token_hash == token_hash(token),
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
        )
    ).first()
    if row is None:
        raise Problem(401, "unauthenticated", "authentication required")
    sess, user = row
    # Touch last_seen at most once a minute to avoid a write per request.
    if sess.last_seen_at is None or now - sess.last_seen_at > timedelta(minutes=1):
        await session.execute(update(Session).where(Session.id == sess.id).values(last_seen_at=now))
        await session.commit()
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def get_policy(settings: SettingsDep) -> SsrfPolicy:
    return SsrfPolicy.from_settings(
        settings.allowed_ports, settings.ssrf_allow_private_cidrs, settings.allowed_hosts
    )


def get_resolver() -> Resolver:
    return SystemResolver()
