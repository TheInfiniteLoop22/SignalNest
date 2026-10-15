from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
# expire_on_commit=False: attributes stay readable after commit without an implicit
# lazy-load, which is illegal in async code (it would need an await).
SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
