from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
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
        await conn.execute(text("TRUNCATE users, monitors, monitor_state, check_runs CASCADE"))
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
