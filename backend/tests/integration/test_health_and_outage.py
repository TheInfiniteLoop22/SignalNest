"""Liveness vs readiness, and the worker surviving a database outage."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from api_support import LOCAL_POLICY, make_client
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Monitor, User
from app.db.session import get_session
from app.worker.loop import Worker

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db(migrated_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_db, pool_size=10)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    await engine.dispose()


async def test_healthz_ignores_the_database_and_readyz_depends_on_it(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, app):
        assert (await c.get("/healthz")).json() == {"status": "ok"}
        assert (await c.get("/readyz")).json() == {"status": "ready"}

        async def broken() -> AsyncIterator[AsyncSession]:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))
            yield  # pragma: no cover

        app.dependency_overrides[get_session] = broken
        assert (await c.get("/healthz")).status_code == 200  # liveness unaffected
        r = await c.get("/readyz")
        assert r.status_code == 503
        assert r.json()["reason"] == "database"


class FlakySessions:
    """A sessionmaker stand-in that fails every call while `down` is set."""

    def __init__(self, real: async_sessionmaker[AsyncSession]) -> None:
        self.real = real
        self.down = False

    def __call__(self) -> AsyncSession:
        if self.down:
            raise OSError("simulated: database unreachable")
        return self.real()


async def test_worker_survives_a_db_outage_and_recovers_without_restart(  # type: ignore[no-untyped-def]
    faultline: str, db
) -> None:
    flaky = FlakySessions(db)
    async with db() as s:
        u = User(email=f"{uuid.uuid4().hex[:8]}@t.test", password_hash="!")
        s.add(u)
        await s.flush()
        m = Monitor(owner_id=u.id, name="m", url=f"{faultline}/ok", interval_seconds=30)
        s.add(m)
        await s.commit()
        mid = m.id

    worker = Worker(
        flaky,  # type: ignore[arg-type]
        policy=LOCAL_POLICY,
        poll_interval_s=0.05,
        db_retry_s=0.2,
        shutdown_grace_s=1,
    )
    flaky.down = True
    task = asyncio.create_task(worker.run())

    await asyncio.sleep(1.2)
    assert not task.done()  # the process stays alive during the outage
    async with db() as s:
        assert await s.scalar(text("SELECT count(*) FROM check_runs")) == 0

    flaky.down = False  # database is back
    n = 0
    for _ in range(60):
        await asyncio.sleep(0.1)
        async with db() as s:
            n = (
                await s.scalar(
                    text("SELECT count(*) FROM check_runs WHERE monitor_id = :m"), {"m": mid}
                )
                or 0
            )
        if n:
            break
    worker.request_stop()
    await asyncio.wait_for(task, 5)
    assert n == 1
