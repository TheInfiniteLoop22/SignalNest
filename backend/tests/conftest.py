import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn
from alembic import command
from alembic.config import Config
from faultline.app import app as faultline_app
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Monitor, MonitorState, User

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Migrate the database down to base and back to head once per session.

    This exercises both upgrade and downgrade of every migration.
    """
    cfg = Config(str(ALEMBIC_INI))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return get_settings().database_url


@pytest_asyncio.fixture
async def session(migrated_db: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(migrated_db)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE users, monitors, monitor_state, check_runs, worker_heartbeats CASCADE")
        )
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(session: AsyncSession) -> tuple[User, Monitor]:
    user = User(email=get_settings().dev_user_email)
    session.add(user)
    await session.flush()
    monitor = Monitor(owner_id=user.id, name="m", url="http://faultline.test/ok")
    session.add(monitor)
    await session.flush()
    session.add(MonitorState(monitor_id=monitor.id))
    await session.commit()
    return user, monitor


@pytest_asyncio.fixture
async def faultline() -> AsyncIterator[str]:
    """Faultline on an ephemeral localhost port; yields the base URL."""
    config = uvicorn.Config(
        faultline_app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes no startup Event
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    server.force_exit = True
    try:
        await asyncio.wait_for(task, 3)
    except (TimeoutError, asyncio.CancelledError):
        pass  # abandoned connections (e.g. /hang) must not stall the suite
