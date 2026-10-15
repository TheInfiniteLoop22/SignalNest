"""M8: health endpoints, metrics, system status, retention, DB-outage recovery."""

import asyncio
import base64
import ipaddress
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from api_support import LOCAL_POLICY, make_client, register_and_login
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Monitor, MonitorState, User
from app.db.session import get_session
from app.observability import metrics
from app.probe.ssrf import SsrfPolicy
from app.worker import retention
from app.worker.loop import Worker

pytestmark = pytest.mark.integration

V1 = "/api/v1"
LOCAL = SsrfPolicy(allowed_ports=None, allow_private_cidrs=(ipaddress.ip_network("127.0.0.0/8"),))


@pytest_asyncio.fixture
async def db(migrated_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_db, pool_size=10)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users, worker_heartbeats, job_runs CASCADE"))
    await engine.dispose()


async def make_monitor(maker: async_sessionmaker[AsyncSession], url: str, **kw: Any) -> uuid.UUID:
    async with maker() as s:
        u = User(email=f"{uuid.uuid4().hex[:8]}@t.test", password_hash="!")
        s.add(u)
        await s.flush()
        m = Monitor(owner_id=u.id, name="m", url=url, interval_seconds=30, timeout_ms=2000, **kw)
        s.add(m)
        await s.flush()
        s.add(MonitorState(monitor_id=m.id, next_run_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
        return m.id


_seq = iter(range(1, 1_000_000))


async def insert_run(
    maker: async_sessionmaker[AsyncSession],
    monitor_id: uuid.UUID,
    age_days: float,
    outcome: str = "PASS",
) -> int:
    # unique slot per call even on a coarse clock
    at = datetime.now(UTC) - timedelta(days=age_days, microseconds=next(_seq))
    fc = None if outcome in ("PASS", "DEGRADED") else "READ_TIMEOUT"
    async with maker() as s:
        rid = await s.scalar(
            text(
                """INSERT INTO check_runs (monitor_id, scheduled_for, started_at, finished_at,
                       outcome, failure_class, worker_id, trigger)
                   VALUES (:m, :at, :at, :at, :o, :fc, 'w', 'schedule') RETURNING id"""
            ),
            {"m": monitor_id, "at": at, "o": outcome, "fc": fc},
        )
        await s.commit()
    return int(rid)  # type: ignore[arg-type]


# --- health / metrics ------------------------------------------------------------------------


async def test_healthz_never_touches_the_database_and_readyz_does(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
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


async def test_readyz_times_out_instead_of_hanging(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, app):

        async def slow() -> AsyncIterator[AsyncSession]:
            await asyncio.sleep(5)
            yield None  # type: ignore[misc]

        app.dependency_overrides[get_session] = slow
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        r = await c.get("/readyz")
        assert r.status_code == 503
        assert loop.time() - t0 < 2


async def test_metrics_exposes_route_templates_not_concrete_paths(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        fake = str(uuid.uuid4())
        await c.get(f"{V1}/monitors/{fake}")
        body = (await c.get("/metrics")).text
    assert 'route="/api/v1/monitors/{monitor_id}"' in body
    assert fake not in body
    assert "signalnest_http_request_duration_seconds_bucket" in body


async def test_metrics_basic_auth(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db, metrics_basic_auth="prom:s3cret") as (c, _):
        assert (await c.get("/metrics")).status_code == 401
        good = base64.b64encode(b"prom:s3cret").decode()
        bad = base64.b64encode(b"prom:nope").decode()
        assert (
            await c.get("/metrics", headers={"Authorization": f"Basic {good}"})
        ).status_code == 200
        assert (
            await c.get("/metrics", headers={"Authorization": f"Basic {bad}"})
        ).status_code == 401


async def test_request_log_line_has_correlation_and_no_query_string(  # type: ignore[no-untyped-def]
    migrated_db: str, db, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="signalnest.http")
    async with make_client(migrated_db) as (c, _):
        r = await c.get(f"{V1}/auth/me?secret=hunter2", headers={"X-Request-ID": "trace-42"})
        assert r.headers["x-request-id"] == "trace-42"
    recs = [x for x in caplog.records if x.name == "signalnest.http"]
    assert recs
    rec = recs[-1]
    assert rec.route == "/api/v1/auth/me"  # type: ignore[attr-defined]
    assert rec.status == 401  # type: ignore[attr-defined]
    assert "hunter2" not in json.dumps(rec.__dict__, default=str)


async def test_system_status_reports_workers_lag_and_inconclusive(migrated_db: str, db) -> None:  # type: ignore[no-untyped-def]
    async with make_client(migrated_db) as (c, _):
        await register_and_login(c)
        me = (await c.get(f"{V1}/auth/me")).json()
        async with db() as s:
            await s.execute(
                text(
                    """INSERT INTO worker_heartbeats
                           (worker_id, started_at, last_seen_at, version, in_flight)
                       VALUES ('alive', now(), now(), '0', 3), ('dead', now() - interval '1 hour',
                              now() - interval '10 minutes', '0', 0)"""
                )
            )
            m = Monitor(owner_id=uuid.UUID(me["id"]), name="m", url="http://x.example.com/",
                        interval_seconds=30, timeout_ms=2000)  # fmt: skip
            s.add(m)
            await s.flush()
            s.add(
                MonitorState(monitor_id=m.id, next_run_at=datetime.now(UTC) - timedelta(seconds=90))
            )
            await s.commit()
            mid = m.id
        await insert_run(db, mid, 0, "INCONCLUSIVE")

        st = (await c.get(f"{V1}/system/status")).json()
        by_id = {w["worker_id"]: w for w in st["workers"]}
        assert by_id["alive"]["alive"] is True and by_id["alive"]["in_flight"] == 3
        assert by_id["dead"]["alive"] is False
        assert st["scheduler"]["due_count"] == 1
        assert 80 <= st["scheduler"]["lag_seconds"] <= 120
        assert st["inconclusive_last_hour"]["count"] == 1
        assert st["retention"] is None


# --- retention ------------------------------------------------------------------------------


async def test_retention_deletes_old_runs_but_keeps_incident_evidence(db) -> None:  # type: ignore[no-untyped-def]
    mid = await make_monitor(db, "http://x.example.com/")
    old = [await insert_run(db, mid, 30) for _ in range(7)]
    keep_first = await insert_run(db, mid, 40, "FAIL")  # referenced by an incident
    fresh = await insert_run(db, mid, 1)
    async with db() as s:
        await s.execute(
            text(
                """INSERT INTO incidents (monitor_id, status, opened_at, first_failure_run_id,
                       failure_count, dominant_failure_class)
                   VALUES (:m, 'RESOLVED', now() - interval '40 days', :r, 1, 'READ_TIMEOUT')"""
            ),
            {"m": mid, "r": keep_first},
        )
        await s.commit()

    now = datetime.now(UTC)
    async with db() as s:
        assert await retention.preview(s, retention_days=14, now=now) == len(old)  # dry run
    result = await retention.run_retention(db, retention_days=14, now=now)
    assert result.runs == len(old)

    async with db() as s:
        remaining = {r[0] for r in await s.execute(text("SELECT id FROM check_runs"))}
    assert remaining == {keep_first, fresh}


async def test_retention_deletes_in_multiple_batches(db, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(retention, "BATCH", 3)
    mid = await make_monitor(db, "http://x.example.com/")
    for _ in range(8):
        await insert_run(db, mid, 30)
    result = await retention.run_retention(db, retention_days=14, now=datetime.now(UTC))
    assert result.runs == 8  # 3 + 3 + 2: looped until a short batch


async def test_retention_also_clears_stale_sessions_and_heartbeats(db) -> None:  # type: ignore[no-untyped-def]
    await make_monitor(db, "http://x.example.com/")
    async with db() as s:
        uid = await s.scalar(text("SELECT id FROM users LIMIT 1"))
        await s.execute(
            text(
                """INSERT INTO sessions (user_id, token_hash, expires_at) VALUES
                   (:u, '\\x01', now() - interval '10 days'),
                   (:u, '\\x02', now() + interval '1 day')"""
            ),
            {"u": uid},
        )
        await s.execute(
            text(
                """INSERT INTO worker_heartbeats (worker_id, started_at, last_seen_at, version)
                   VALUES ('old', now(), now() - interval '2 hours', '0'),
                          ('new', now(), now(), '0')"""
            )
        )
        await s.commit()
    r = await retention.run_retention(db, retention_days=14, now=datetime.now(UTC))
    assert (r.sessions, r.heartbeats) == (1, 1)


async def test_only_one_of_many_concurrent_workers_runs_retention(db) -> None:  # type: ignore[no-untyped-def]
    results = await asyncio.gather(
        *(
            retention.run_if_due(db, worker_id=f"w{i}", every_s=3600, retention_days=14)
            for i in range(5)
        )
    )
    assert sum(r is not None for r in results) == 1
    async with db() as s:
        rows = (await s.execute(text("SELECT finished_at, error FROM job_runs"))).all()
    assert len(rows) == 1
    assert rows[0].finished_at is not None and rows[0].error is None
    # and it is not due again within the interval
    assert await retention.run_if_due(db, worker_id="w9", every_s=3600, retention_days=14) is None


# --- DB outage (deferred from M4) ------------------------------------------------------------


class FlakySessions:
    """A sessionmaker stand-in that fails every call while `down` is set."""

    def __init__(self, real: async_sessionmaker[AsyncSession]) -> None:
        self.real = real
        self.down = False
        self.failures = 0

    def __call__(self) -> AsyncSession:
        if self.down:
            self.failures += 1
            raise OSError("simulated: database unreachable")
        return self.real()


async def test_worker_backs_off_during_db_outage_and_recovers_without_restart(  # type: ignore[no-untyped-def]
    faultline: str, db
) -> None:
    flaky = FlakySessions(db)
    mid = await make_monitor(db, f"{faultline}/ok")
    worker = Worker(
        flaky,  # type: ignore[arg-type]
        policy=LOCAL_POLICY,
        worker_id="w1",
        poll_interval_s=0.05,
        heartbeat_interval_s=0.1,
        db_backoff_min_s=0.2,
        db_backoff_max_s=0.4,
        retention_every_s=3600,
        shutdown_grace_s=1,
        jitter_s=10.0,
    )
    before = metrics.DB_ERRORS.labels("claim")._value.get()  # noqa: SLF001
    flaky.down = True
    task = asyncio.create_task(worker.run())

    await asyncio.sleep(1.5)
    assert not task.done()  # the process stays alive
    claim_errors = metrics.DB_ERRORS.labels("claim")._value.get() - before  # noqa: SLF001
    assert 2 <= claim_errors <= 8  # it retried, with backoff rather than in a hot loop
    async with db() as s:
        assert await s.scalar(text("SELECT count(*) FROM check_runs")) == 0

    flaky.down = False  # database is back
    for _ in range(60):
        await asyncio.sleep(0.1)
        async with db() as s:
            n = await s.scalar(
                text("SELECT count(*) FROM check_runs WHERE monitor_id = :m"), {"m": mid}
            )
        if n:
            break
    worker.request_stop()
    await asyncio.wait_for(task, 5)
    assert n == 1
    async with db() as s:
        dupes = await s.scalar(
            text(
                "SELECT count(*) FROM (SELECT 1 FROM check_runs GROUP BY monitor_id, scheduled_for "
                "HAVING count(*) > 1) d"
            )
        )
    assert dupes == 0


_ = httpx
