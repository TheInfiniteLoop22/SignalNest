from collections.abc import AsyncIterator

import httpx
import pytest
from faultline.app import app as faultline_app
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Monitor, User
from app.db.session import get_session
from app.main import create_app
from app.services.probe_once import probe_monitor

pytestmark = pytest.mark.integration


def api_client(session: AsyncSession) -> httpx.AsyncClient:
    # Reuse the test's session so the app's global engine (bound to an earlier
    # event loop) is never touched.
    app = create_app()

    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_probe_three_times_and_list_via_api(
    session: AsyncSession, seeded: tuple[User, Monitor]
) -> None:
    _, monitor = seeded
    transport = httpx.ASGITransport(app=faultline_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://faultline.test") as client:
        for _ in range(3):
            run = await probe_monitor(session, monitor.id, client=client, worker_id="test")
            assert run.outcome == "PASS"
            assert run.http_status == 200

    async with api_client(session) as api:
        resp = await api.get(f"/api/v1/monitors/{monitor.id}/runs")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert len(ids) == 3
    assert ids == sorted(ids, reverse=True)  # newest first


async def test_unknown_monitor_is_404(session: AsyncSession, seeded: tuple[User, Monitor]) -> None:
    async with api_client(session) as api:
        resp = await api.get("/api/v1/monitors/00000000-0000-0000-0000-000000000000/runs")
    assert resp.status_code == 404


async def test_unreachable_target_is_recorded_as_fail(
    session: AsyncSession, seeded: tuple[User, Monitor]
) -> None:
    _, monitor = seeded

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        run = await probe_monitor(session, monitor.id, client=client, worker_id="test")
    assert run.outcome == "FAIL"
    assert run.failure_class == "UNCLASSIFIED_ERROR"
