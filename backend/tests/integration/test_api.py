import asyncio
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
        assert "at least 12" in r.json()["detail"]
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


async def test_errors_have_a_readable_detail(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        r = await c.get(f"{V1}/monitors")
        assert r.status_code == 401
        assert r.json() == {"detail": "authentication required"}


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
        assert "already exists" in r.json()["detail"]


async def test_per_user_monitor_cap(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, max_monitors_per_user=1) as (c, _):
        await register_and_login(c)
        await create_monitor(c, "one")
        r = await c.post(f"{V1}/monitors", json=monitor_body("two"))
        assert r.status_code == 422 and "monitor limit" in r.json()["detail"]


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "ftp://example.com/",
        "http://user:pw@example.com/",
    ],
)
async def test_ssrf_precheck_rejects_at_create(migrated_db: str, db, url: str) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, policy=SsrfPolicy()) as (c, _):
        await register_and_login(c)
        r = await c.post(f"{V1}/monitors", json=monitor_body(url=url))
        assert r.status_code == 422, r.text


async def test_ssrf_precheck_rejects_names_that_resolve_private(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(
        migrated_db, policy=SsrfPolicy(), resolver=StaticResolver(["10.0.0.7"])
    ) as (c, _):
        await register_and_login(c)
        r = await c.post(f"{V1}/monitors", json=monitor_body(url="https://sneaky.example.com/"))
        assert r.status_code == 422
        assert "non-public" in r.json()["detail"]


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


# --- login throttle ---------------------------------------------------------------------------


async def test_login_is_locked_after_repeated_failures(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await c.post(f"{V1}/auth/register", json={"email": "a@example.com", "password": PASSWORD})
        wrong = {"email": "a@example.com", "password": "x" * 12}
        for _ in range(5):
            assert (await c.post(f"{V1}/auth/login", json=wrong)).status_code == 401
        locked = await c.post(f"{V1}/auth/login", json=wrong)
        assert locked.status_code == 429
        assert 1 <= int(locked.headers["retry-after"]) <= 900
        # Even the right password is refused while locked, so guessing gains nothing.
        right = await c.post(
            f"{V1}/auth/login", json={"email": "a@example.com", "password": PASSWORD}
        )
        assert right.status_code == 429
        # Other accounts are unaffected.
        await c.post(f"{V1}/auth/register", json={"email": "b@example.com", "password": PASSWORD})
        other = await c.post(
            f"{V1}/auth/login", json={"email": "b@example.com", "password": PASSWORD}
        )
        assert other.status_code == 204


async def test_unknown_emails_are_throttled_the_same_way(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        body = {"email": "ghost@example.com", "password": "x" * 12}
        statuses = [(await c.post(f"{V1}/auth/login", json=body)).status_code for _ in range(7)]
        assert statuses == [401] * 5 + [429] * 2  # indistinguishable from a real account


async def test_a_successful_login_resets_the_failure_count(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await c.post(f"{V1}/auth/register", json={"email": "a@example.com", "password": PASSWORD})
        wrong = {"email": "a@example.com", "password": "x" * 12}
        right = {"email": "a@example.com", "password": PASSWORD}
        for _ in range(4):
            await c.post(f"{V1}/auth/login", json=wrong)
        assert (await c.post(f"{V1}/auth/login", json=right)).status_code == 204
        for _ in range(4):  # a fresh allowance of failures
            assert (await c.post(f"{V1}/auth/login", json=wrong)).status_code == 401


async def test_password_hash_is_upgraded_at_login(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    async with db() as s:
        await s.execute(
            text("INSERT INTO users (email, password_hash) VALUES (:e, :h)"),
            {"e": "old@example.com", "h": weak},
        )
        await s.commit()
    async with make_client(migrated_db) as (c, _):
        r = await c.post(
            f"{V1}/auth/login", json={"email": "old@example.com", "password": PASSWORD}
        )
        assert r.status_code == 204  # the weak hash still verifies
    async with db() as s:
        stored = await s.scalar(text("SELECT password_hash FROM users"))
    assert stored != weak and "m=65536" in stored  # replaced by a hash with current settings


# --- cross-origin writes ---------------------------------------------------------------------


async def test_cross_origin_writes_are_refused(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        evil = await c.post(
            f"{V1}/monitors", json=monitor_body(), headers={"Origin": "https://evil.example"}
        )
        assert evil.status_code == 403
        assert evil.json() == {"detail": "cross-origin request refused"}
        same = await c.post(
            f"{V1}/monitors", json=monitor_body(), headers={"Origin": "http://test"}
        )
        assert same.status_code == 201  # the app's own origin is fine
        # Reads are never blocked by the origin check.
        assert (
            await c.get(f"{V1}/monitors", headers={"Origin": "https://evil.example"})
        ).status_code == 200


# --- input handling --------------------------------------------------------------------------


async def test_unknown_fields_are_rejected(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        r = await c.post(
            f"{V1}/monitors", json=monitor_body(owner_id="00000000-0000-0000-0000-000000000000")
        )
        assert r.status_code == 422
        assert r.json()["detail"].startswith("owner_id:")
        mid = (await create_monitor(c, "ok"))["id"]
        patch = await c.patch(f"{V1}/monitors/{mid}", json={"colour": "red"})
        assert patch.status_code == 422


async def test_validation_messages_name_the_field(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        r = await c.post(f"{V1}/monitors", json=monitor_body(timeout_ms=10))
        assert r.status_code == 422
        assert r.json()["detail"].startswith("timeout_ms:")
        short = await c.post(
            f"{V1}/auth/register", json={"email": "z@example.com", "password": "short"}
        )
        assert short.json()["detail"].startswith("password:")


async def test_runs_and_incidents_can_be_paged(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c))["id"]
        await insert_runs(db, mid, ["PASS"] * 5)
        page1 = (await c.get(f"{V1}/monitors/{mid}/runs?limit=2")).json()
        page2 = (await c.get(f"{V1}/monitors/{mid}/runs?limit=2&offset=2")).json()
        page3 = (await c.get(f"{V1}/monitors/{mid}/runs?limit=2&offset=4")).json()
        ids = [r["id"] for r in page1 + page2 + page3]
        assert len(ids) == 5 and len(set(ids)) == 5  # no overlap, nothing missed
        assert ids == sorted(ids, reverse=True)  # still newest first across pages
        assert (await c.get(f"{V1}/monitors/{mid}/runs?offset=-1")).status_code == 422
        assert (await c.get(f"{V1}/incidents?offset=0")).status_code == 200


async def test_the_monitor_cap_holds_when_creates_race(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, max_monitors_per_user=1) as (c, _):
        await register_and_login(c)
        results = await asyncio.gather(
            *(c.post(f"{V1}/monitors", json=monitor_body(f"m{i}")) for i in range(6))
        )
        assert sorted(r.status_code for r in results) == [201] + [422] * 5
        assert len((await c.get(f"{V1}/monitors")).json()) == 1


async def test_blocked_ports_are_rejected_at_create(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, policy=SsrfPolicy()) as (c, _):
        await register_and_login(c)
        for url in ("http://example.com:22/", "https://example.com:6379/"):
            r = await c.post(f"{V1}/monitors", json=monitor_body(url=url))
            assert r.status_code == 422 and "not allowed" in r.json()["detail"]
        ok = await c.post(f"{V1}/monitors", json=monitor_body(url="https://example.com:8443/"))
        assert ok.status_code == 201
