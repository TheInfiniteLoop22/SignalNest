"""Shared FastAPI dependencies: auth, CSRF header, limiters, SSRF policy, resolver."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Problem
from app.api.ratelimit import FixedWindowLimiter
from app.config import Settings, get_settings
from app.db.models import Session, User
from app.db.session import get_session
from app.probe.engine import SystemResolver, run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import ProbeRequest, ProbeResult, Resolver
from app.security import token_hash

COOKIE = "sn_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "signalnest"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DbSession = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_csrf_header(request: Request) -> None:
    """Unsafe methods must carry a custom header a cross-site form cannot set."""
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise Problem(403, "csrf", "missing X-Requested-With header")


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


def limiter(name: str) -> Callable[[Request], FixedWindowLimiter]:
    def dep(request: Request) -> FixedWindowLimiter:
        return request.app.state.limiters[name]  # type: ignore[no-any-return]

    return dep


def enforce(limiter_: FixedWindowLimiter, key: str) -> None:
    if not limiter_.hit(key):
        raise Problem(
            429,
            "rate-limited",
            "too many requests",
            headers={"Retry-After": str(limiter_.retry_after(key))},
        )


def get_policy(settings: SettingsDep) -> SsrfPolicy:
    return SsrfPolicy.from_settings(
        settings.allowed_ports, settings.ssrf_allow_private_cidrs, settings.allowed_hosts
    )


def get_resolver() -> Resolver:
    return SystemResolver()


Prober = Callable[[ProbeRequest], Awaitable[ProbeResult]]


def get_prober(
    policy: Annotated[SsrfPolicy, Depends(get_policy)],
    resolver: Annotated[Resolver, Depends(get_resolver)],
) -> Prober:
    async def probe(req: ProbeRequest) -> ProbeResult:
        return await run_probe(req, resolver=resolver, policy=policy)

    return probe
