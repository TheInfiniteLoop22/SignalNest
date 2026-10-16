"""Login throttle backed by Postgres, so every API process shares one view of the failures.

Failures are counted per email (whether or not the account exists, so the response never
reveals which emails are registered). While an email is locked, further attempts are refused
before the password is checked and are not counted, so the lock cannot be extended forever.
"""

import math

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Problem


async def ensure_login_allowed(
    session: AsyncSession, email: str, *, max_failures: int, window_s: int
) -> None:
    """Raise 429 with Retry-After when `email` has too many recent failed logins."""
    row = (
        await session.execute(
            text(
                """
                SELECT count(*) AS n,
                       EXTRACT(EPOCH FROM (min(failed_at) + make_interval(secs => :w) - now()))
                           AS wait
                FROM login_failures
                WHERE email = :e AND failed_at > now() - make_interval(secs => :w)
                """
            ),
            {"e": email, "w": window_s},
        )
    ).one()
    if row.n >= max_failures:
        retry_after = max(1, math.ceil(float(row.wait or window_s)))
        raise Problem(
            429,
            "too-many-attempts",
            "too many failed logins; try again later",
            headers={"Retry-After": str(retry_after)},
        )


async def record_login_failure(session: AsyncSession, email: str, *, window_s: int) -> None:
    """Store one failure and drop this email's rows that no longer count."""
    await session.execute(
        text(
            "DELETE FROM login_failures "
            "WHERE email = :e AND failed_at <= now() - make_interval(secs => :w)"
        ),
        {"e": email, "w": window_s},
    )
    await session.execute(text("INSERT INTO login_failures (email) VALUES (:e)"), {"e": email})
    await session.commit()


async def clear_login_failures(session: AsyncSession, email: str) -> None:
    await session.execute(text("DELETE FROM login_failures WHERE email = :e"), {"e": email})
