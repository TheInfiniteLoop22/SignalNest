import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from api_support import (
    CSRF,
    PASSWORD,
    StaticResolver,
    fake_result,
    make_client,
    monitor_body,
    register_and_login,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.probe.ssrf import SsrfPolicy
from app.probe.types import Outcome

pytestmark = pytest.mark.integration

V1 = "/api/v1"


@pytest_asyncio.fixture
async def db(migrated_db: str):  # type: ignore[no-untyped-def]
    engine = create_async_engine(migrated_db)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users, worker_heartbeats CASCADE"))
    await engine.dispose()


async def create_monitor(client: httpx.AsyncClient, name: str = "orders", **over: Any) -> dict:  # type: ignore[type-arg]
    r = await client.post(f"{V1}/monitors", json=monitor_body(name, **over))
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def insert_runs(
    maker: async_sessionmaker[AsyncSession],
    monitor_id: str,
    specs: list[tuple[str, str | None, int | None]],
    *,
    start: datetime | None = None,
) -> list[int]:
    """specs: (outcome, failure_class, total_ms); spaced one minute apart, oldest first."""
    start = start or (datetime.now(UTC) - timedelta(minutes=len(specs) + 1))
    ids: list[int] = []
    async with maker() as s:
        for i, (outcome, fc, ms) in enumerate(specs):
            at = start + timedelta(minutes=i)
            rid = await s.scalar(
                text(
                    """
                    INSERT INTO check_runs (monitor_id, scheduled_for, started_at, finished_at,
                        outcome, failure_class, http_status, total_ms, worker_id, trigger)
                    VALUES (:m, :at, :at, :at, :o, :fc, :st, :ms, 'w', 'schedule')
                    RETURNING id
                    """
                ),
                {"m": monitor_id, "at": at, "o": outcome, "fc": fc, "st": 200 if not fc else None,
                 "ms": ms},
            )  # fmt: skip
            ids.append(int(rid))  # type: ignore[arg-type]
        await s.commit()
    return ids


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


async def test_login_errors_are_generic_and_rate_limited(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
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
        for _ in range(4):  # 1 failure for a@ so far (z@ is a different key) + 4 = 5
            await c.post(f"{V1}/auth/login", json={"email": "a@example.com", "password": "x" * 12})
        r = await c.post(f"{V1}/auth/login", json={"email": "a@example.com", "password": PASSWORD})
        assert r.status_code == 429  # even the right password is refused while locked out
        assert int(r.headers["retry-after"]) > 0


async def test_unsafe_requests_need_the_csrf_header(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        r = await c.post(f"{V1}/monitors", json=monitor_body(), headers={"X-Requested-With": ""})
        assert r.status_code == 403
        assert r.json()["type"].endswith("/csrf")


async def test_errors_are_problem_json_with_request_id(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        r = await c.get(f"{V1}/monitors", headers={"X-Request-ID": "abc-123"})
        assert r.status_code == 401
        body = r.json()
        assert r.headers["content-type"].startswith("application/problem+json")
        assert body["status"] == 401 and body["request_id"] == "abc-123"
        assert r.headers["x-request-id"] == "abc-123"
        assert body["instance"] == f"{V1}/monitors"


# --- monitors -----------------------------------------------------------------------------


async def test_monitor_crud_roundtrip(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        m = await create_monitor(
            c,
            assertions=[{"type": "json_equals", "path": "status", "value": "ok"}],
            headers={"Accept": "application/json"},
            degraded_ms=800,
        )
        assert m["state"]["health"] == "UNKNOWN"
        assert m["assertions"] == [{"type": "json_equals", "path": "status", "value": "ok"}]
        mid = m["id"]

        listed = (await c.get(f"{V1}/monitors")).json()
        assert [x["id"] for x in listed] == [mid]
        assert listed[0]["summary_24h"]["sample_count"] == 0

        r = await c.patch(
            f"{V1}/monitors/{mid}", json={"name": "orders-v2", "interval_seconds": 120}
        )
        assert r.status_code == 200
        assert r.json()["name"] == "orders-v2" and r.json()["interval_seconds"] == 120

        assert (await c.delete(f"{V1}/monitors/{mid}")).status_code == 204
        assert (await c.get(f"{V1}/monitors/{mid}")).status_code == 404


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"headers": {"Host": "evil"}}, "headers"),
        ({"headers": {"Authorization": "x"}}, "headers"),
        ({"headers": {"Proxy-Foo": "x"}}, "headers"),
        ({"headers": {"bad name": "x"}}, "headers"),
        ({"timeout_ms": 5000, "degraded_ms": 5000}, ""),
        ({"interval_seconds": 0}, "interval_seconds"),
        ({"method": "POST"}, "method"),
        ({"assertions": [{"type": "nope"}]}, "assertions"),
        ({"failure_threshold": 11}, "failure_threshold"),
    ],
)
async def test_monitor_validation(migrated_db: str, db, override, field) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        r = await c.post(f"{V1}/monitors", json=monitor_body(**override))
        assert r.status_code == 422, r.text
        assert r.json()["type"].endswith("/validation")


async def test_minimum_interval_is_enforced(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, min_interval_s=30) as (c, _):
        await register_and_login(c)
        r = await c.post(f"{V1}/monitors", json=monitor_body(interval_seconds=10))
        assert r.status_code == 422
        r = await c.post(f"{V1}/monitors", json=monitor_body(interval_seconds=30))
        assert r.status_code == 201


async def test_duplicate_name_is_conflict(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        await create_monitor(c, "same")
        r = await c.post(f"{V1}/monitors", json=monitor_body("same"))
        assert r.status_code == 409
        assert r.json()["type"].endswith("/duplicate-name")


async def test_per_user_and_per_host_caps(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, max_monitors_per_user=2, max_monitors_per_host=1) as (c, _):
        await register_and_login(c)
        await create_monitor(c, "one", url="https://a.example.com/x")
        r = await c.post(f"{V1}/monitors", json=monitor_body("two", url="https://a.example.com/y"))
        assert r.status_code == 422 and r.json()["type"].endswith("/host-limit")
        await create_monitor(c, "two", url="https://b.example.com/")
        r = await c.post(f"{V1}/monitors", json=monitor_body("three", url="https://c.example.com/"))
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
async def test_ssrf_precheck_rejects_at_create_ac6(migrated_db: str, db, url: str) -> None:  # type: ignore[no-untyped-def]
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


async def test_pause_resume_check_now(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c))["id"]

        assert (await c.post(f"{V1}/monitors/{mid}/pause")).status_code == 204
        assert (await c.get(f"{V1}/monitors/{mid}")).json()["state"]["health"] == "PAUSED"
        assert (await c.post(f"{V1}/monitors/{mid}/check")).status_code == 409

        assert (await c.post(f"{V1}/monitors/{mid}/resume")).status_code == 204
        assert (await c.post(f"{V1}/monitors/{mid}/resume")).status_code == 204  # idempotent
        assert (await c.get(f"{V1}/monitors/{mid}")).json()["state"]["health"] == "UNKNOWN"

        assert (await c.post(f"{V1}/monitors/{mid}/check")).status_code == 202
        again = await c.post(f"{V1}/monitors/{mid}/check")
        assert again.status_code == 429 and "retry-after" in again.headers
    async with db() as s:
        due = await s.scalar(
            text("SELECT next_run_at <= now() FROM monitor_state WHERE monitor_id = :m"),
            {"m": mid},
        )
    assert due is True


async def test_interval_edit_pulls_next_run_forward(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c, interval_seconds=3600))["id"]
        async with db() as s:
            await s.execute(
                text("UPDATE monitor_state SET next_run_at = now() + interval '1 hour'"),
            )
            await s.commit()
        await c.patch(f"{V1}/monitors/{mid}", json={"interval_seconds": 60})
    async with db() as s:
        secs = await s.scalar(
            text("SELECT extract(epoch FROM next_run_at - now()) FROM monitor_state")
        )
    assert secs is not None and secs <= 60


async def test_deleting_a_monitor_with_an_incident_works(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c))["id"]
        [rid] = await insert_runs(db, mid, [("FAIL", "READ_TIMEOUT", 5000)])
        async with db() as s:
            await s.execute(
                text(
                    """INSERT INTO incidents (monitor_id, status, opened_at, first_failure_run_id,
                       failure_count, dominant_failure_class)
                       VALUES (:m, 'OPEN', now(), :r, 1, 'READ_TIMEOUT')"""
                ),
                {"m": mid, "r": rid},
            )
            await s.commit()
        assert (await c.delete(f"{V1}/monitors/{mid}")).status_code == 204


# --- runs, stats --------------------------------------------------------------------------


async def test_runs_keyset_pagination_has_no_gaps_or_duplicates(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c))["id"]
        ids = await insert_runs(db, mid, [("PASS", None, 50)] * 25)

        seen: list[int] = []
        cursor = None
        pages = 0
        while True:
            params: dict[str, Any] = {"limit": 10}
            if cursor:
                params["cursor"] = cursor
            page = (await c.get(f"{V1}/monitors/{mid}/runs", params=params)).json()
            seen += [r["id"] for r in page["items"]]
            pages += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert pages == 3
        assert seen == sorted(ids, reverse=True)  # newest first, each exactly once

        # rows arriving between page fetches do not shift the next page (offset would)
        first = (await c.get(f"{V1}/monitors/{mid}/runs", params={"limit": 10})).json()
        await insert_runs(db, mid, [("PASS", None, 50)], start=datetime.now(UTC))
        second = (
            await c.get(
                f"{V1}/monitors/{mid}/runs", params={"limit": 10, "cursor": first["next_cursor"]}
            )
        ).json()
        assert [r["id"] for r in second["items"]] == sorted(ids, reverse=True)[10:20]


async def test_runs_filters_and_bad_cursor(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c))["id"]
        await insert_runs(
            db,
            mid,
            [("PASS", None, 40), ("FAIL", "READ_TIMEOUT", 5000), ("FAIL", "DNS_FAILURE", None)],
        )
        r = await c.get(f"{V1}/monitors/{mid}/runs", params={"outcome": "FAIL"})
        assert {x["failure_class"] for x in r.json()["items"]} == {"READ_TIMEOUT", "DNS_FAILURE"}
        r = await c.get(f"{V1}/monitors/{mid}/runs", params={"failure_class": "DNS_FAILURE"})
        assert len(r.json()["items"]) == 1
        r = await c.get(f"{V1}/monitors/{mid}/runs", params={"cursor": "garbage"})
        assert r.status_code == 422
        r = await c.get(f"{V1}/monitors/{mid}/runs", params={"limit": 500})
        assert r.status_code == 422


async def test_stats_exclude_inconclusive_from_uptime(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c))["id"]
        await insert_runs(
            db,
            mid,
            [("PASS", None, 100)] * 6
            + [("DEGRADED", None, 900)]
            + [("FAIL", "READ_TIMEOUT", 5000)] * 3
            + [("INCONCLUSIVE", "PROBE_INTERNAL_ERROR", 1)] * 5,
        )
        s = (await c.get(f"{V1}/monitors/{mid}/stats", params={"window": "24h"})).json()
        assert s["sample_count"] == 10  # 5 inconclusive excluded
        assert s["uptime_pct"] == 70.0  # 7 healthy / 10
        assert s["by_outcome"] == {"PASS": 6, "DEGRADED": 1, "FAIL": 3, "INCONCLUSIVE": 5}
        assert s["by_failure_class"]["READ_TIMEOUT"] == 3
        assert s["p50_ms"] == 100.0  # failures' 5000 ms do not leak into latency
        assert sum(p["sample_count"] for p in s["series"]) == 10
        r = await c.get(f"{V1}/monitors/{mid}/stats", params={"window": "30d"})
        assert r.status_code == 422


async def test_run_detail_has_explanation(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c))["id"]
        [rid] = await insert_runs(db, mid, [("FAIL", "READ_TIMEOUT", 5000)])
        r = await c.get(f"{V1}/runs/{rid}")
        assert r.status_code == 200
        body = r.json()
        assert "did not send response headers" in body["explanation"]
        assert body["timings"]["total_ms"] == 5000
        assert body["monitor_id"] == mid


# --- incidents ----------------------------------------------------------------------------


async def test_incident_list_open_first_and_detail(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        mid = (await create_monitor(c))["id"]
        runs = await insert_runs(db, mid, [("FAIL", "HTTP_UNEXPECTED_STATUS", 20)] * 3)
        now = datetime.now(UTC)
        async with db() as s:
            ids = []
            for status, opened in (
                ("RESOLVED", now - timedelta(days=2)),
                ("OPEN", now - timedelta(days=1)),
            ):
                iid = await s.scalar(
                    text(
                        """INSERT INTO incidents (monitor_id, status, opened_at,
                               first_failure_run_id, failure_count, dominant_failure_class)
                           VALUES (:m, :s, :o, :r, 3, 'HTTP_UNEXPECTED_STATUS') RETURNING id"""
                    ),
                    {"m": mid, "s": status, "o": opened, "r": runs[0]},
                )
                ids.append(iid)
            await s.execute(
                text(
                    "INSERT INTO incident_events (incident_id, at, kind, run_id) "
                    "VALUES (:i, now(), 'OPENED', :r)"
                ),
                {"i": ids[1], "r": runs[2]},
            )
            await s.commit()

        page = (await c.get(f"{V1}/incidents")).json()
        assert [i["status"] for i in page["items"]] == ["OPEN", "RESOLVED"]
        only_open = (await c.get(f"{V1}/incidents", params={"status": "OPEN"})).json()
        assert len(only_open["items"]) == 1

        p1 = (await c.get(f"{V1}/incidents", params={"limit": 1})).json()
        assert [i["status"] for i in p1["items"]] == ["OPEN"] and p1["next_cursor"]
        p2 = (
            await c.get(f"{V1}/incidents", params={"limit": 1, "cursor": p1["next_cursor"]})
        ).json()
        assert [i["status"] for i in p2["items"]] == ["RESOLVED"] and p2["next_cursor"] is None

        detail = (await c.get(f"{V1}/incidents/{ids[1]}")).json()
        assert detail["events"][0]["kind"] == "OPENED"
        assert detail["evidence"]["class_counts"] == {"HTTP_UNEXPECTED_STATUS": 3}
        assert detail["evidence"]["baseline"]["sample_count"] == 0
        assert detail["first_failure_run_id"] == runs[0]


# --- probe test ---------------------------------------------------------------------------


async def test_probe_test_returns_unsaved_result_and_is_rate_limited(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def prober(req):  # type: ignore[no-untyped-def]
        calls.append(req.url)
        return fake_result(Outcome.PASS)

    async with make_client(migrated_db, prober=prober) as (c, _):
        await register_and_login(c)
        body = {"url": "https://api.example.com/health", "timeout_ms": 3000}
        r = await c.post(f"{V1}/probe/test", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["outcome"] == "PASS"
        assert out["timings"]["connect_ms"] == 11
        assert out["response_snippet"] == '{"ok":true}'
        for _ in range(5):
            assert (await c.post(f"{V1}/probe/test", json=body)).status_code == 200
        limited = await c.post(f"{V1}/probe/test", json=body)
        assert limited.status_code == 429  # 6 per minute
        assert len(calls) == 6
    async with db() as s:
        assert await s.scalar(text("SELECT count(*) FROM check_runs")) == 0  # nothing stored


async def test_probe_test_blocks_private_targets_before_sending(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    sent: list[str] = []

    async def prober(req):  # type: ignore[no-untyped-def]
        sent.append(req.url)
        return fake_result()

    async with make_client(migrated_db, policy=SsrfPolicy(), prober=prober) as (c, _):
        await register_and_login(c)
        r = await c.post(f"{V1}/probe/test", json={"url": "http://169.254.169.254/"})
        assert r.status_code == 422
        assert sent == []
        r = await c.post(
            f"{V1}/probe/test", json={"url": "https://x.example.com/", "timeout_ms": 60000}
        )
        assert r.status_code == 422  # timeout capped at 10 s


# --- ownership (IDOR) ---------------------------------------------------------------------


async def test_other_users_resources_are_404_everywhere_ac8(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    """Route crawler: every route that takes an id must 404 for a user who does not own it."""
    async with make_client(migrated_db) as (alice, app):
        await register_and_login(alice, "alice@example.com")
        mid = (await create_monitor(alice))["id"]
        run_ids = await insert_runs(db, mid, [("FAIL", "READ_TIMEOUT", 5000)] * 2)
        async with db() as s:
            iid = await s.scalar(
                text(
                    """INSERT INTO incidents (monitor_id, status, opened_at, first_failure_run_id,
                           failure_count, dominant_failure_class)
                       VALUES (:m, 'OPEN', now(), :r, 2, 'READ_TIMEOUT') RETURNING id"""
                ),
                {"m": mid, "r": run_ids[0]},
            )
            await s.commit()

        substitutions = {"monitor_id": mid, "run_id": str(run_ids[0]), "incident_id": str(iid)}
        # The OpenAPI document is the framework-independent list of every route and method.
        routes = [
            (path, method.upper())
            for path, ops in app.openapi()["paths"].items()
            if "{" in path
            for method in ops
        ]
        assert len(routes) >= 10, routes  # the crawler must actually find the id routes

        async with make_client(migrated_db) as (bob, _):
            await register_and_login(bob, "bob@example.com")
            for path, method in routes:
                url = path
                for name, value in substitutions.items():
                    url = url.replace("{" + name + "}", value)
                assert "{" not in url, f"unmapped path parameter in {path}"
                resp = await bob.request(
                    method, url, json={} if method in ("PATCH", "POST") else None
                )
                assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"

            # and bob sees none of alice's data in list endpoints
            assert (await bob.get(f"{V1}/monitors")).json() == []
            assert (await bob.get(f"{V1}/incidents")).json()["items"] == []

        # alice's monitor is untouched
        assert (await alice.get(f"{V1}/monitors/{mid}")).status_code == 200
        assert uuid.UUID(mid)


_ = CSRF
