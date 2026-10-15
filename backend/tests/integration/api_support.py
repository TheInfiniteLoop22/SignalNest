"""Helpers for API tests: an app wired to the test database with controllable seams."""

import ipaddress
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_policy, get_resolver
from app.config import Settings, get_settings
from app.db.session import get_session
from app.main import create_app
from app.probe.ssrf import SsrfPolicy

PASSWORD = "correct-horse-battery"

LOCAL_POLICY = SsrfPolicy(
    allowed_ports=None,
    allow_private_cidrs=(ipaddress.ip_network("127.0.0.0/8"),),
    allowed_hosts=frozenset({"faultline"}),
)


class StaticResolver:
    """Resolves every name to the given answer (default: a public address)."""

    def __init__(self, ips: list[str] | None = None) -> None:
        self.ips = ips if ips is not None else ["93.184.216.34"]

    async def resolve(self, host: str, timeout_s: float) -> list[str]:
        return self.ips


@asynccontextmanager
async def make_client(
    database_url: str,
    *,
    policy: SsrfPolicy | None = None,
    resolver: StaticResolver | None = None,
    **settings: Any,
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    engine = create_async_engine(database_url, pool_size=10)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    cfg = Settings(**{"min_interval_s": 1, "cookie_secure": False, **settings})  # type: ignore[arg-type]

    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: cfg
    app.dependency_overrides[get_policy] = lambda: policy or LOCAL_POLICY
    app.dependency_overrides[get_resolver] = lambda: resolver or StaticResolver()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, app
    await engine.dispose()


async def register_and_login(
    client: httpx.AsyncClient, email: str = "a@example.com", password: str = PASSWORD
) -> None:
    r = await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 204, r.text


def monitor_body(name: str = "orders", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name, "url": "https://api.example.com/orders"}
    body.update(over)
    return body
