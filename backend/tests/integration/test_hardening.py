"""Login throttle, cross-origin guard, retention and webhook alerts, against a real database."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from api_support import PASSWORD, StaticResolver, make_client, monitor_body, register_and_login
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_worker import cycle, make_monitors, result

from app.config import get_settings
from app.probe.ssrf import SsrfPolicy
from app.worker.alerts import claim_alerts, deliver, record_delivery
from app.worker.retention import run_maintenance

pytestmark = pytest.mark.integration

V1 = "/api/v1"
Sessions = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def sessions(migrated_db: str):  # type: ignore[no-untyped-def]
    engine = create_async_engine(migrated_db, pool_size=10)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
        await conn.execute(text("TRUNCATE login_failures"))
    await engine.dispose()


async def login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post(f"{V1}/auth/login", json={"email": email, "password": password})


# --- login throttle -----------------------------------------------------------------------


async def test_repeated_failed_logins_lock_the_email(sessions: Sessions, migrated_db: str) -> None:
    async with make_client(migrated_db, login_max_failures=3) as (client, _):
        await register_and_login(client, "a@example.com")
        for _ in range(3):
            assert (await login(client, "a@example.com", "wrong-password")).status_code == 401

        locked = await login(client, "a@example.com", "wrong-password")
        assert locked.status_code == 429
        assert locked.headers["content-type"].startswith("application/problem+json")
        assert int(locked.headers["retry-after"]) > 0

        # The right password does not skip the lock, and other emails are unaffected.
        assert (await login(client, "a@example.com", PASSWORD)).status_code == 429
        await register_and_login(client, "b@example.com")


async def test_unknown_emails_are_throttled_too(sessions: Sessions, migrated_db: str) -> None:
    async with make_client(migrated_db, login_max_failures=2) as (client, _):
        for _ in range(2):
            assert (await login(client, "ghost@example.com", "whatever-1234")).status_code == 401
        assert (await login(client, "ghost@example.com", "whatever-1234")).status_code == 429


async def test_a_successful_login_resets_the_counter(sessions: Sessions, migrated_db: str) -> None:
    async with make_client(migrated_db, login_max_failures=3) as (client, _):
        await register_and_login(client, "a@example.com")
        for _ in range(2):
            await login(client, "a@example.com", "wrong-password")
        assert (await login(client, "a@example.com", PASSWORD)).status_code == 204
        for _ in range(2):  # would be the 3rd and 4th failure without the reset
            assert (await login(client, "a@example.com", "wrong-password")).status_code == 401


async def test_the_lock_expires(sessions: Sessions, migrated_db: str) -> None:
    async with make_client(migrated_db, login_max_failures=2) as (client, _):
        await register_and_login(client, "a@example.com")
        for _ in range(2):
            await login(client, "a@example.com", "wrong-password")
        assert (await login(client, "a@example.com", PASSWORD)).status_code == 429
        async with sessions() as s:
            await s.execute(text("UPDATE login_failures SET failed_at = now() - interval '1 day'"))
            await s.commit()
        assert (await login(client, "a@example.com", PASSWORD)).status_code == 204


# --- cross-origin guard -------------------------------------------------------------------


async def test_cross_origin_writes_are_refused(sessions: Sessions, migrated_db: str) -> None:
    async with make_client(migrated_db) as (client, _):
        await register_and_login(client)
        body = monitor_body()

        evil = await client.post(
            f"{V1}/monitors", json=body, headers={"Origin": "https://evil.test"}
        )
        assert evil.status_code == 403
        assert (await client.get(f"{V1}/monitors")).json() == []  # nothing was created

        same = await client.post(f"{V1}/monitors", json=body, headers={"Origin": "http://test"})
        assert same.status_code == 201


async def test_trusted_origins_are_allowed(
    sessions: Sessions, migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The middleware reads settings once, when the app is built, so set the environment first.
    monkeypatch.setenv("TRUSTED_ORIGINS", "https://app.example.com")
    get_settings.cache_clear()
    try:
        async with make_client(migrated_db) as (client, _):
            await register_and_login(client)
            r = await client.post(
                f"{V1}/monitors", json=monitor_body(), headers={"Origin": "https://app.example.com"}
            )
            assert r.status_code == 201
    finally:
        monkeypatch.delenv("TRUSTED_ORIGINS")
        get_settings.cache_clear()


# --- retention ----------------------------------------------------------------------------


async def test_maintenance_removes_only_old_data(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions)
    async with sessions() as s:
        for age_days in (45, 1):
            await s.execute(
                text(
                    "INSERT INTO check_runs (monitor_id, started_at, outcome, total_ms) "
                    "VALUES (:m, now() - make_interval(days => :d), 'PASS', 5)"
                ),
                {"m": mid, "d": age_days},
            )
        await s.execute(
            text(
                "INSERT INTO login_failures (email, failed_at) VALUES "
                "('x@t.test', now() - interval '2 days'), ('y@t.test', now())"
            )
        )
        await s.commit()

    async with sessions() as s:
        removed = await run_maintenance(s, retention_days=30)

    assert removed["check_runs"] == 1
    assert removed["login_failures"] == 1
    async with sessions() as s:
        left = await s.scalar(text("SELECT count(*) FROM check_runs"))
    assert left == 1


# --- webhook alerts -----------------------------------------------------------------------

HOOK = "https://hooks.example.com/services/abc"
PUBLIC = SsrfPolicy()


async def set_webhook(sessions: Sessions, monitor_id: object, url: str | None = HOOK) -> None:
    async with sessions() as s:
        await s.execute(
            text("UPDATE monitors SET webhook_url = :u WHERE id = :id"),
            {"u": url, "id": monitor_id},
        )
        await s.commit()


async def alert_rows(sessions: Sessions) -> list[dict[str, object]]:
    async with sessions() as s:
        rows = await s.execute(text("SELECT * FROM alerts ORDER BY id"))
        return [dict(r) for r in rows.mappings()]


async def test_opening_and_resolving_an_incident_queue_one_alert_each(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, threshold=2)
    await set_webhook(sessions, mid)

    await cycle(sessions, mid, result(False))
    assert await alert_rows(sessions) == []  # below the threshold: nothing to say yet
    await cycle(sessions, mid, result(False))
    await cycle(sessions, mid, result(False))  # still the same incident: no second alert
    await cycle(sessions, mid, result(True))

    alerts = await alert_rows(sessions)
    assert [a["kind"] for a in alerts] == ["OPEN", "RESOLVE"]
    assert alerts[0]["payload"]["event"] == "incident.opened"  # type: ignore[index]
    assert alerts[0]["payload"]["failure_class"] == "CONNECT"  # type: ignore[index]
    assert alerts[1]["payload"]["event"] == "incident.resolved"  # type: ignore[index]


async def test_no_alert_without_a_webhook(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, threshold=1)
    await cycle(sessions, mid, result(False))
    assert await alert_rows(sessions) == []


async def test_a_queued_alert_is_delivered_as_a_json_post(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, threshold=1)
    await set_webhook(sessions, mid)
    await cycle(sessions, mid, result(False))

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    async with sessions() as s:
        (alert,) = await claim_alerts(s, limit=10)
    async with sessions() as s:
        assert await claim_alerts(s, limit=10) == []  # leased: no other worker takes it

    error = await deliver(
        alert, policy=PUBLIC, resolver=StaticResolver(), transport=httpx.MockTransport(handler)
    )
    assert error is None
    assert seen[0].method == "POST"
    assert seen[0].headers["host"] == "hooks.example.com"
    assert json.loads(seen[0].content)["event"] == "incident.opened"

    async with sessions() as s:
        await record_delivery(s, alert, error, max_attempts=5)
    assert (await alert_rows(sessions))[0]["status"] == "DELIVERED"


async def test_failed_delivery_is_retried_with_backoff_then_gives_up(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, threshold=1)
    await set_webhook(sessions, mid)
    await cycle(sessions, mid, result(False))

    fail = httpx.MockTransport(lambda r: httpx.Response(500))
    for attempt in (1, 2):
        async with sessions() as s:
            (alert,) = await claim_alerts(s, limit=10)
        error = await deliver(alert, policy=PUBLIC, resolver=StaticResolver(), transport=fail)
        assert error is not None and "BAD_RESPONSE" in error
        async with sessions() as s:
            await record_delivery(s, alert, error, max_attempts=2)
        row = (await alert_rows(sessions))[0]
        if attempt == 1:
            assert row["status"] == "PENDING" and row["attempts"] == 1
            assert row["next_attempt_at"] > datetime.now(UTC) + timedelta(seconds=20)  # type: ignore[operator]
            async with sessions() as s:  # skip the wait
                await s.execute(text("UPDATE alerts SET next_attempt_at = now()"))
                await s.commit()
    assert row["status"] == "FAILED" and row["attempts"] == 2


async def test_webhooks_cannot_target_internal_addresses(
    sessions: Sessions, migrated_db: str
) -> None:
    async with make_client(migrated_db, policy=PUBLIC) as (client, _):
        await register_and_login(client)
        r = await client.post(
            f"{V1}/monitors",
            json=monitor_body(webhook_url="http://169.254.169.254/latest/meta-data"),
        )
        assert r.status_code == 422
        ok = await client.post(f"{V1}/monitors", json=monitor_body(webhook_url=HOOK))
        assert ok.status_code == 201 and ok.json()["webhook_url"] == HOOK

    (mid,) = await make_monitors(sessions, threshold=1)
    await set_webhook(sessions, mid, "http://169.254.169.254/")  # even if one slipped in
    await cycle(sessions, mid, result(False))
    async with sessions() as s:
        (alert,) = await claim_alerts(s, limit=10)
    error = await deliver(alert, policy=PUBLIC, resolver=StaticResolver(["169.254.169.254"]))
    assert error is not None and error.startswith("BLOCKED")
