import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    COOKIE,
    CurrentUser,
    DbSession,
    SettingsDep,
    enforce,
    limiter,
    require_csrf_header,
)
from app.api.errors import Problem
from app.api.ratelimit import FixedWindowLimiter
from app.api.schemas import Credentials, RegisterIn, UserOut
from app.db.models import Session, User
from app.security import hash_password, new_session_token, token_hash, verify_password

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_csrf_header)])

LoginLimiter = Annotated[FixedWindowLimiter, Depends(limiter("login"))]


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/register", status_code=201, response_model=UserOut)
async def register(body: RegisterIn, session: DbSession, settings: SettingsDep) -> User:
    if not settings.allow_registration:
        raise Problem(403, "registration-disabled", "registration is disabled")
    # argon2 is deliberately slow (CPU); keep it off the event loop.
    pw_hash = await asyncio.to_thread(hash_password, body.password)
    user = User(email=body.email, password_hash=pw_hash)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Problem(409, "email-taken", "an account with that email already exists") from exc
    return user


@router.post("/login", status_code=204)
async def login(
    body: Credentials,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    failures: LoginLimiter,
) -> None:
    key = f"{body.email}|{_client_ip(request)}"
    if failures.blocked(key):
        raise Problem(
            429,
            "rate-limited",
            "too many failed logins, try again later",
            headers={"Retry-After": str(failures.retry_after(key))},
        )
    user = await session.scalar(select(User).where(User.email == body.email))
    ok = await asyncio.to_thread(
        verify_password, body.password, user.password_hash if user else None
    )
    if user is None or not ok:
        failures.hit(key)
        raise Problem(401, "invalid-credentials", "invalid credentials")

    failures.reset(key)
    token = new_session_token()
    now = datetime.now(UTC)
    session.add(
        Session(
            user_id=user.id,
            token_hash=token_hash(token),
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
        )
    )
    await session.commit()
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: DbSession) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        await session.execute(
            update(Session)
            .where(Session.token_hash == token_hash(token), Session.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
    response.delete_cookie(COOKIE, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


__all__ = ["enforce", "router"]
