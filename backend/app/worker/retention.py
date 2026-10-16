"""Housekeeping: delete data nobody needs any more, in small batches.

Batches keep each transaction short and lock-light, so cleanup never stalls probing.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BATCH = 5000

# table -> filter. Every table has an `id` primary key, which the batched DELETE targets.
_TARGETS = {
    "check_runs": "started_at < now() - make_interval(days => :days)",
    "alerts": "status <> 'PENDING' AND created_at < now() - make_interval(days => :days)",
    "login_failures": "failed_at < now() - interval '1 day'",
}


async def _purge(session: AsyncSession, table: str, where: str, days: int) -> int:
    total = 0
    while True:
        result = await session.execute(
            text(
                f"DELETE FROM {table} WHERE id IN "  # noqa: S608 - table/where are constants
                f"(SELECT id FROM {table} WHERE {where} LIMIT :n)"
            ),
            {"days": days, "n": BATCH},
        )
        await session.commit()
        deleted = int(result.rowcount)  # type: ignore[attr-defined]
        total += deleted
        if deleted < BATCH:
            return total


async def run_maintenance(session: AsyncSession, *, retention_days: int) -> dict[str, int]:
    """Prune old runs, finished alerts and stale login failures; drop expired sessions."""
    removed = {t: await _purge(session, t, w, retention_days) for t, w in _TARGETS.items()}
    result = await session.execute(text("DELETE FROM sessions WHERE expires_at < now()"))
    await session.commit()
    removed["sessions"] = int(result.rowcount)  # type: ignore[attr-defined]
    return removed
