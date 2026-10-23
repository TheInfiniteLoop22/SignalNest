import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    COOKIE,
    CurrentUser,
    DbSession,
    SettingsDep,
    get_throttle,
)
from app.api.schemas import Credentials, RegisterIn, UserOut
from app.api.throttle import LoginThrottle
from app.db.models import Session, User
from app.security import (
    hash_password,
    needs_rehash,
    new_session_token,
    token_hash,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201, response_model=UserOut)
async def register(body: RegisterIn, session: DbSession, settings: SettingsDep) -> User:
    if not settings.allow_registration:
        raise HTTPException(403, "registration is disabled")
    # argon2 is deliberately slow (CPU); keep it off the event loop.
    pw_hash = await asyncio.to_thread(hash_password, body.password)
    user = User(email=body.email, password_hash=pw_hash)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "an account with that email already exists") from exc
    return user


@router.post("/login", status_code=204)
async def login(
    body: Credentials,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    throttle: Annotated[LoginThrottle, Depends(get_throttle)],
) -> None:
    wait = throttle.locked_for(body.email)
    if wait:  # refused before the password is even checked, and not counted
        raise HTTPException(
            429,
            "too many failed logins; try again later",
            headers={"Retry-After": str(wait)},
        )
    user = await session.scalar(select(User).where(User.email == body.email))
    ok = await asyncio.to_thread(
        verify_password, body.password, user.password_hash if user else None
    )
    if user is None or not ok:
        throttle.record_failure(body.email)
        raise HTTPException(401, "invalid credentials")
    throttle.clear(body.email)
    if needs_rehash(user.password_hash):  # hashing settings were strengthened since sign-up
        user.password_hash = await asyncio.to_thread(hash_password, body.password)

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
        await session.execute(delete(Session).where(Session.token_hash == token_hash(token)))
        await session.commit()
    response.delete_cookie(COOKIE, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
