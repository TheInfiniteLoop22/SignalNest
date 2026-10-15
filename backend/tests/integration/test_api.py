from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from api_support import (
    PASSWORD,
    StaticResolver,
    make_client,
    monitor_body,
    register_and_login,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.probe.ssrf import SsrfPolicy

pytestmark = pytest.mark.integration

V1 = "/api/v1"


@pytest_asyncio.fixture
async def db(migrated_db: str):  # type: ignore[no-untyped-def]
    engine = create_async_engine(migrated_db)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    await engine.dispose()


async def create_monitor(client: httpx.AsyncClient, name: str = "orders", **over: Any) -> dict:  # type: ignore[type-arg]
    r = await client.post(f"{V1}/monitors", json=monitor_body(name, **over))
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def insert_runs(
    maker: async_sessionmaker[AsyncSession], monitor_id: str, outcomes: list[str]
) -> None:
    """One run per outcome, spaced a minute apart, oldest first."""
    start = datetime.now(UTC) - timedelta(minutes=len(outcomes) + 1)
    async with maker() as s:
        for i, outcome in enumerate(outcomes):
            await s.execute(
                text(
                    """
                    INSERT INTO check_runs
                        (monitor_id, started_at, outcome, failure_class, http_status, total_ms)
                    VALUES (:m, :at, :o, :fc, :st, 50)
                    """
                ),
                {
                    "m": monitor_id,
                    "at": start + timedelta(minutes=i),
                    "o": outcome,
                    "fc": None if outcome == "PASS" else "CONNECT",
                    "st": 200 if outcome == "PASS" else None,
                },
            )
        await s.commit()


# --- auth ---------------------------------------------------------------------------------


async def test_register_login_me_logout(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        assert (await c.get(f"{V1}/auth/me")).status_code == 401
        await register_and_login(c)
        me = await c.get(f"{V1}/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "a@example.com"
        assert "password" not in me.text
        assert (await c.post(f"{V1}/auth/logout")).status_code == 204
        assert (await c.get(f"{V1}/auth/me")).status_code == 401


async def test_session_cookie_flags_and_hashed_token(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, cookie_secure=True) as (c, _):
        await c.post(f"{V1}/auth/register", json={"email": "a@example.com", "password": PASSWORD})
        r = await c.post(f"{V1}/auth/login", json={"email": "a@example.com", "password": PASSWORD})
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
        raw = r.cookies.get("sn_session") or cookie.split("sn_session=")[1].split(";")[0]
    async with db() as s:
        stored = await s.scalar(text("SELECT token_hash FROM sessions"))
    assert stored is not None
    assert raw.encode() not in bytes(stored)  # only a hash is stored
    assert len(bytes(stored)) == 32


async def test_register_validation_conflict_and_disabled(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        r = await c.post(
            f"{V1}/auth/register", json={"email": "a@example.com", "password": "short"}
        )
        assert r.status_code == 422
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["errors"][0]["field"] == "password"
        r = await c.post(f"{V1}/auth/register", json={"email": "nope", "password": PASSWORD})
        assert r.status_code == 422
        await register_and_login(c, "Dup@Example.com")
        r = await c.post(
            f"{V1}/auth/register", json={"email": "dup@example.com", "password": PASSWORD}
        )
        assert r.status_code == 409  # email is case-normalised
    async with make_client(migrated_db, allow_registration=False) as (c, _):
        r = await c.post(
            f"{V1}/auth/register", json={"email": "b@example.com", "password": PASSWORD}
        )
        assert r.status_code == 403


async def test_login_errors_are_generic(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await c.post(f"{V1}/auth/register", json={"email": "a@example.com", "password": PASSWORD})
        wrong = await c.post(
            f"{V1}/auth/login", json={"email": "a@example.com", "password": "x" * 12}
        )
        unknown = await c.post(
            f"{V1}/auth/login", json={"email": "z@example.com", "password": "x" * 12}
        )
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_errors_are_problem_json(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        r = await c.get(f"{V1}/monitors")
        assert r.status_code == 401
        body = r.json()
        assert r.headers["content-type"].startswith("application/problem+json")
        assert body["status"] == 401
        assert body["type"].endswith("/unauthenticated")
        assert body["instance"] == f"{V1}/monitors"


# --- monitors -----------------------------------------------------------------------------


async def test_monitor_crud(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        m = await create_monitor(c, expected_status=200, body_contains="ok")
        assert m["status"] == "UNKNOWN"
        assert m["uptime_24h_pct"] is None
        mid = m["id"]

        assert [x["id"] for x in (await c.get(f"{V1}/monitors")).json()] == [mid]
        r = await c.patch(
            f"{V1}/monitors/{mid}", json={"name": "orders-v2", "expected_status": None}
        )
        assert r.status_code == 200
        assert r.json()["name"] == "orders-v2"
        assert r.json()["expected_status"] is None
        assert r.json()["body_contains"] == "ok"  # untouched fields stay

        assert (await c.delete(f"{V1}/monitors/{mid}")).status_code == 204
        assert (await c.get(f"{V1}/monitors/{mid}")).status_code == 404


@pytest.mark.parametrize(
    "override",
    [
        {"timeout_ms": 10},
        {"interval_seconds": 0},
        {"expected_status": 42},
        {"failure_threshold": 11},
        {"body_contains": ""},
    ],
)
async def test_monitor_validation(migrated_db: str, db, override) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        r = await c.post(f"{V1}/monitors", json=monitor_body(**override))
        assert r.status_code == 422, r.text


async def test_minimum_interval_is_enforced(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, min_interval_s=30) as (c, _):
        await register_and_login(c)
        too_fast = await c.post(f"{V1}/monitors", json=monitor_body(interval_seconds=10))
        assert too_fast.status_code == 422
        ok = await c.post(f"{V1}/monitors", json=monitor_body(interval_seconds=30))
        assert ok.status_code == 201


async def test_duplicate_name_is_conflict(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        await create_monitor(c, "same")
        r = await c.post(f"{V1}/monitors", json=monitor_body("same"))
        assert r.status_code == 409
        assert r.json()["type"].endswith("/duplicate-name")


async def test_per_user_monitor_cap(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, max_monitors_per_user=1) as (c, _):
        await register_and_login(c)
        await create_monitor(c, "one")
        r = await c.post(f"{V1}/monitors", json=monitor_body("two"))
        assert r.status_code == 422 and r.json()["type"].endswith("/monitor-limit")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://2130706433/",
        "http://localhost/",
        "http://example.com:22/",
        "ftp://example.com/",
        "http://user:pw@example.com/",
    ],
)
async def test_ssrf_precheck_rejects_at_create(migrated_db: str, db, url: str) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, policy=SsrfPolicy()) as (c, _):
        await register_and_login(c)
        r = await c.post(f"{V1}/monitors", json=monitor_body(url=url))
        assert r.status_code == 422, r.text
        assert r.json()["type"].rsplit("/", 1)[1] in {"blocked-target", "invalid-target"}


async def test_ssrf_precheck_rejects_names_that_resolve_private(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(
        migrated_db, policy=SsrfPolicy(), resolver=StaticResolver(["10.0.0.7"])
    ) as (c, _):
        await register_and_login(c)
        r = await c.post(f"{V1}/monitors", json=monitor_body(url="https://sneaky.example.com/"))
        assert r.status_code == 422
        assert r.json()["type"].endswith("/blocked-target")


# --- runs, uptime, incidents ----------------------------------------------------------------


async def test_runs_are_newest_first_and_uptime_is_computed(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c))["id"]
        await insert_runs(db, mid, ["PASS", "PASS", "PASS", "FAIL"])

        runs = (await c.get(f"{V1}/monitors/{mid}/runs", params={"limit": 3})).json()
        assert [r["outcome"] for r in runs] == ["FAIL", "PASS", "PASS"]
        assert runs[0]["failure_class"] == "CONNECT"

        assert (await c.get(f"{V1}/monitors/{mid}")).json()["uptime_24h_pct"] == 75.0
        listed = (await c.get(f"{V1}/monitors")).json()
        assert listed[0]["uptime_24h_pct"] == 75.0


async def test_incidents_list_open_first_with_filter(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c))["id"]
        async with db() as s:
            await s.execute(
                text(
                    """
                    INSERT INTO incidents (monitor_id, opened_at, resolved_at, failure_class)
                    VALUES (:m, now() - interval '2 hours', now() - interval '1 hour', 'TLS'),
                           (:m, now() - interval '10 minutes', NULL, 'TIMEOUT')
                    """
                ),
                {"m": mid},
            )
            await s.commit()

        items = (await c.get(f"{V1}/incidents")).json()
        assert [i["status"] for i in items] == ["OPEN", "RESOLVED"]
        assert items[0]["monitor_name"] == "orders"
        assert items[0]["failure_class"] == "TIMEOUT"
        only = (await c.get(f"{V1}/incidents", params={"status": "RESOLVED"})).json()
        assert [i["failure_class"] for i in only] == ["TLS"]


# --- ownership (IDOR) ---------------------------------------------------------------------


async def test_other_users_resources_are_404(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (alice, _):
        await register_and_login(alice, "alice@example.com")
        mid = (await create_monitor(alice))["id"]
        await insert_runs(db, mid, ["FAIL"])
        async with db() as s:
            await s.execute(
                text(
                    "INSERT INTO incidents (monitor_id, opened_at, failure_class) "
                    "VALUES (:m, now(), 'CONNECT')"
                ),
                {"m": mid},
            )
            await s.commit()

        async with make_client(migrated_db) as (bob, _):
            await register_and_login(bob, "bob@example.com")
            assert (await bob.get(f"{V1}/monitors")).json() == []
            assert (await bob.get(f"{V1}/incidents")).json() == []
            for method, path in [
                ("GET", f"/monitors/{mid}"),
                ("PATCH", f"/monitors/{mid}"),
                ("DELETE", f"/monitors/{mid}"),
                ("GET", f"/monitors/{mid}/runs"),
            ]:
                body = {} if method == "PATCH" else None
                r = await bob.request(method, f"{V1}{path}", json=body)
                assert r.status_code == 404, (method, path, r.status_code)

        assert (await alice.get(f"{V1}/monitors/{mid}")).status_code == 200  # still there
