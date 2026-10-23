"""The worker loop and the incident lifecycle, against a real database."""

import asyncio
import ipaddress
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Monitor, User
from app.incidents.state_machine import Action
from app.probe.ssrf import SsrfPolicy
from app.probe.types import FailureClass, Outcome, ProbeResult
from app.worker import loop as loop_module
from app.worker.loop import Worker, record_result

pytestmark = pytest.mark.integration

LOCAL = SsrfPolicy(allow_private_cidrs=(ipaddress.ip_network("127.0.0.0/8"),), allowed_ports=None)

Sessions = async_sessionmaker[AsyncSession]


class PublicResolver:
    """Every name resolves to a public address, so fake hosts pass the SSRF check."""

    async def resolve(self, host: str, timeout_s: float) -> list[str]:
        return ["93.184.216.34"]


@pytest_asyncio.fixture
async def sessions(migrated_db: str) -> AsyncIterator[Sessions]:
    engine = create_async_engine(migrated_db, pool_size=20)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    await engine.dispose()


async def make_monitors(
    sessions: Sessions,
    n: int = 1,
    *,
    url: str = "http://faultline.test/ok",
    threshold: int = 3,
    webhook_url: str | None = None,
) -> list[uuid.UUID]:
    async with sessions() as s:
        user = User(email=f"{uuid.uuid4().hex[:8]}@t.test", password_hash="!")
        s.add(user)
        await s.flush()
        monitors = [
            Monitor(
                owner_id=user.id,
                name=f"m{i}",
                url=url,
                interval_seconds=30,
                failure_threshold=threshold,
                webhook_url=webhook_url,
                next_run_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            for i in range(n)
        ]
        s.add_all(monitors)
        await s.commit()
        return [m.id for m in monitors]


def result(ok: bool, failure: FailureClass = FailureClass.CONNECT) -> ProbeResult:
    return ProbeResult(
        outcome=Outcome.PASS if ok else Outcome.FAIL,
        failure_class=None if ok else failure,
        http_status=200 if ok else None,
        resolved_ip=None,
        dns_ms=None,
        connect_ms=None,
        tls_ms=None,
        ttfb_ms=None,
        total_ms=5,
    )


async def record(sessions: Sessions, monitor_id: uuid.UUID, res: ProbeResult) -> Action:
    """Save one result for the monitor, as the worker would after probing it."""
    async with sessions() as s:
        monitor = await s.get(Monitor, monitor_id)
        assert monitor is not None
        return await record_result(s, monitor, res, started_at=datetime.now(UTC))


async def monitor_row(sessions: Sessions, monitor_id: uuid.UUID) -> dict[str, object]:
    async with sessions() as s:
        row = await s.execute(text("SELECT * FROM monitors WHERE id = :id"), {"id": monitor_id})
        return dict(row.mappings().one())


async def incidents(sessions: Sessions, monitor_id: uuid.UUID) -> list[dict[str, object]]:
    async with sessions() as s:
        rows = await s.execute(
            text("SELECT * FROM incidents WHERE monitor_id = :id ORDER BY opened_at"),
            {"id": monitor_id},
        )
        return [dict(r) for r in rows.mappings()]


# --- recording a result and the incident lifecycle ----------------------------------------


async def test_recording_stores_the_run_and_schedules_the_next_one(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions)
    await record(sessions, mid, result(True))
    row = await monitor_row(sessions, mid)
    assert row["status"] == "UP"
    assert row["last_checked_at"] is not None
    assert row["next_run_at"] > datetime.now(UTC) + timedelta(seconds=20)  # now + interval
    async with sessions() as s:
        assert await s.scalar(text("SELECT count(*) FROM check_runs")) == 1


async def test_failures_below_the_threshold_open_no_incident(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, threshold=3)
    for ok in (True, False, False):
        await record(sessions, mid, result(ok))
    assert (await monitor_row(sessions, mid))["status"] == "UP"
    assert await incidents(sessions, mid) == []


async def test_outage_opens_one_incident_and_recovery_resolves_it(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, threshold=3)
    await record(sessions, mid, result(True))
    actions = [await record(sessions, mid, result(False, FailureClass.TIMEOUT)) for _ in range(5)]
    assert actions == [Action.NONE, Action.NONE, Action.OPEN, Action.NONE, Action.NONE]

    (inc,) = await incidents(sessions, mid)  # five failures, still a single incident
    assert inc["resolved_at"] is None
    assert inc["failure_class"] == "TIMEOUT"
    assert (await monitor_row(sessions, mid))["status"] == "DOWN"

    assert await record(sessions, mid, result(True)) is Action.RESOLVE
    (inc,) = await incidents(sessions, mid)
    assert inc["resolved_at"] is not None
    assert (await monitor_row(sessions, mid))["status"] == "UP"


async def test_a_second_outage_is_a_second_incident(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, threshold=1)
    for ok in (False, True, False, True):
        await record(sessions, mid, result(ok))
    assert len(await incidents(sessions, mid)) == 2


async def test_recording_for_a_deleted_monitor_is_a_no_op(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions)
    async with sessions() as s:
        monitor = await s.get(Monitor, mid)
    async with sessions() as s:
        await s.execute(text("DELETE FROM monitors WHERE id = :id"), {"id": mid})
        await s.commit()
    async with sessions() as s:
        assert monitor is not None
        action = await record_result(s, monitor, result(True), started_at=datetime.now(UTC))
        assert action is Action.NONE


# --- run_once: which monitors get checked -------------------------------------------------


def ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))


async def test_run_once_checks_due_monitors_and_reschedules_them(sessions: Sessions) -> None:
    ids = await make_monitors(sessions, 3)
    worker = Worker(
        sessions, policy=SsrfPolicy(), resolver=PublicResolver(), transport=ok_transport()
    )
    assert await worker.run_once() == 3
    await worker.wait_idle()
    for mid in ids:
        assert (await monitor_row(sessions, mid))["status"] == "UP"
    assert await worker.run_once() == 0  # all rescheduled into the future


async def test_monitors_that_are_not_due_are_skipped(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions)
    async with sessions() as s:
        await s.execute(
            text("UPDATE monitors SET next_run_at = now() + interval '1 hour' WHERE id = :id"),
            {"id": mid},
        )
        await s.commit()
    worker = Worker(
        sessions, policy=SsrfPolicy(), resolver=PublicResolver(), transport=ok_transport()
    )
    assert await worker.run_once() == 0


# --- webhook ------------------------------------------------------------------------------


async def test_webhook_is_sent_when_an_incident_opens_and_resolves(sessions: Sessions) -> None:
    (mid,) = await make_monitors(
        sessions, threshold=1, url="http://site.test/", webhook_url="http://hook.test/in"
    )
    sent: list[str] = []
    site_up = False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            sent.append(request.content.decode())
            return httpx.Response(200)
        return httpx.Response(200 if site_up else 503)

    worker = Worker(
        sessions,
        policy=SsrfPolicy(),
        resolver=PublicResolver(),
        transport=httpx.MockTransport(handler),
    )
    await worker.run_once()  # site down, threshold 1: incident opens
    await worker.wait_idle()
    assert len(sent) == 1 and "incident.opened" in sent[0]

    site_up = True
    async with sessions() as s:
        await s.execute(text("UPDATE monitors SET next_run_at = now() WHERE id = :id"), {"id": mid})
        await s.commit()
    await worker.run_once()
    await worker.wait_idle()
    assert len(sent) == 2 and "incident.resolved" in sent[1]


async def test_a_failing_webhook_does_not_break_the_check(sessions: Sessions) -> None:
    (mid,) = await make_monitors(
        sessions, threshold=1, url="http://site.test/", webhook_url="http://hook.test/in"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500 if request.method == "POST" else 503)

    worker = Worker(
        sessions,
        policy=SsrfPolicy(),
        resolver=PublicResolver(),
        transport=httpx.MockTransport(handler),
    )
    await worker.run_once()
    await worker.wait_idle()
    assert (await monitor_row(sessions, mid))["status"] == "DOWN"


# --- the whole loop, against Faultline ----------------------------------------------------


async def test_worker_probes_a_real_endpoint_and_detects_an_outage(
    sessions: Sessions, faultline: str
) -> None:
    (ok_id,) = await make_monitors(sessions, url=f"{faultline}/ok")
    (bad_id,) = await make_monitors(sessions, url=f"{faultline}/status/503", threshold=1)

    worker = Worker(sessions, policy=LOCAL, poll_interval_s=0.05)
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(100):
            await asyncio.sleep(0.1)
            if (await monitor_row(sessions, bad_id))["status"] == "DOWN":
                break
    finally:
        worker.request_stop()
        await asyncio.wait_for(task, 5)

    assert (await monitor_row(sessions, ok_id))["status"] == "UP"
    (inc,) = await incidents(sessions, bad_id)
    assert inc["failure_class"] == "BAD_RESPONSE"


async def test_cleanup_deletes_only_old_check_runs_and_expired_sessions(
    sessions: Sessions,
) -> None:
    (mid,) = await make_monitors(sessions)
    async with sessions() as s:
        for name, hours in (("expired", -1), ("valid", 1)):
            await s.execute(
                text(
                    "INSERT INTO sessions (user_id, token_hash, expires_at) "
                    "SELECT id, :h, now() + make_interval(hours => :hours) FROM users LIMIT 1"
                ),
                {"h": name.encode(), "hours": hours},
            )
        await s.commit()
    async with sessions() as s:
        for days in (45, 1):
            await s.execute(
                text(
                    "INSERT INTO check_runs (monitor_id, started_at, outcome, total_ms) "
                    "VALUES (:m, now() - make_interval(days => :d), 'PASS', 5)"
                ),
                {"m": mid, "d": days},
            )
        await s.commit()
    await Worker(sessions, policy=SsrfPolicy(), retention_days=30)._cleanup_if_due()
    async with sessions() as s:
        assert await s.scalar(text("SELECT count(*) FROM check_runs")) == 1
        left = (await s.execute(text("SELECT token_hash FROM sessions"))).scalars().all()
        assert [bytes(h) for h in left] == [b"valid"]  # the expired session was purged


# --- one slow or broken monitor never hurts the others --------------------------------------


def host_routed_transport(delays: dict[str, float], broken: set[str] | None = None):  # type: ignore[no-untyped-def]
    """A fake network where requests to a host can be slow, and other hosts answer at once."""

    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        await asyncio.sleep(delays.get(host, 0))
        return httpx.Response(200, text="ok")

    return httpx.MockTransport(handler)


async def test_a_slow_site_does_not_delay_the_others(sessions: Sessions) -> None:
    (slow,) = await make_monitors(sessions, url="http://slow.test/")
    (fast,) = await make_monitors(sessions, url="http://fast.test/")
    worker = Worker(
        sessions,
        policy=SsrfPolicy(),
        resolver=PublicResolver(),
        transport=host_routed_transport({"slow.test": 3}),
    )
    assert await worker.run_once() == 2  # both started; run_once does not wait for them
    await asyncio.sleep(0.5)
    assert (await monitor_row(sessions, fast))["status"] == "UP"  # finished long ago
    assert (await monitor_row(sessions, slow))["status"] == "UNKNOWN"  # still in flight
    assert worker.in_flight == 1
    await worker.wait_idle(0.1, cancel_rest=True)


async def test_a_monitor_that_is_still_being_checked_is_not_started_again(
    sessions: Sessions,
) -> None:
    await make_monitors(sessions, url="http://slow.test/")
    worker = Worker(
        sessions,
        policy=SsrfPolicy(),
        resolver=PublicResolver(),
        transport=host_routed_transport({"slow.test": 3}),
    )
    assert await worker.run_once() == 1
    assert await worker.run_once() == 0  # still due in the database, but already running
    await worker.wait_idle(0.1, cancel_rest=True)


async def test_the_number_of_running_checks_is_capped_by_batch_size(sessions: Sessions) -> None:
    await make_monitors(sessions, 5, url="http://slow.test/")
    worker = Worker(
        sessions,
        policy=SsrfPolicy(),
        resolver=PublicResolver(),
        transport=host_routed_transport({"slow.test": 3}),
        batch_size=2,
    )
    assert await worker.run_once() == 2
    assert await worker.run_once() == 0  # no free slots
    await worker.wait_idle(0.1, cancel_rest=True)


async def test_an_unexpected_error_in_one_check_does_not_stop_the_worker(
    sessions: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    (bad,) = await make_monitors(sessions, url="http://bad.test/")
    (good,) = await make_monitors(sessions, url="http://good.test/")
    real = loop_module.run_probe

    async def flaky_probe(request, **kwargs):  # type: ignore[no-untyped-def]
        if "bad.test" in request.url:
            raise RuntimeError("a bug in our own code")
        return await real(request, **kwargs)

    monkeypatch.setattr(loop_module, "run_probe", flaky_probe)
    worker = Worker(
        sessions,
        policy=SsrfPolicy(),
        resolver=PublicResolver(),
        transport=ok_transport(),
        poll_interval_s=0.05,
    )
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(60):
            await asyncio.sleep(0.1)
            if (await monitor_row(sessions, good))["status"] == "UP":
                break
        assert (await monitor_row(sessions, good))["status"] == "UP"
        assert not task.done()  # the loop is still alive
        assert (await monitor_row(sessions, bad))["status"] == "UNKNOWN"
    finally:
        worker.request_stop()
        await asyncio.wait_for(task, 5)


async def test_the_loop_survives_an_unexpected_error_outside_a_check(
    sessions: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    (mid,) = await make_monitors(sessions)
    worker = Worker(
        sessions,
        policy=SsrfPolicy(),
        resolver=PublicResolver(),
        transport=ok_transport(),
        poll_interval_s=0.05,
    )
    real_run_once = worker.run_once
    calls = 0

    async def run_once_that_breaks_first() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected")
        return await real_run_once()

    monkeypatch.setattr(worker, "run_once", run_once_that_breaks_first)
    task = asyncio.create_task(worker.run())
    try:
        for _ in range(60):
            await asyncio.sleep(0.1)
            if (await monitor_row(sessions, mid))["status"] == "UP":
                break
        assert (await monitor_row(sessions, mid))["status"] == "UP"
        assert calls >= 2 and not task.done()
    finally:
        worker.request_stop()
        await asyncio.wait_for(task, 5)


async def test_stopping_waits_for_running_checks_to_finish(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, url="http://slow.test/")
    worker = Worker(
        sessions,
        policy=SsrfPolicy(),
        resolver=PublicResolver(),
        transport=host_routed_transport({"slow.test": 0.6}),
        poll_interval_s=0.05,
        shutdown_grace_s=5,
    )
    task = asyncio.create_task(worker.run())
    for _ in range(50):
        await asyncio.sleep(0.05)
        if worker.in_flight:
            break
    assert worker.in_flight == 1
    worker.request_stop()
    await asyncio.wait_for(task, 5)
    assert (await monitor_row(sessions, mid))["status"] == "UP"  # the check was allowed to finish


async def test_the_retention_delete_has_an_index_to_use(sessions: Sessions) -> None:
    async with sessions() as s:
        defs = (
            (
                await s.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE tablename = 'check_runs'")
                )
            )
            .scalars()
            .all()
        )
    assert any("(started_at)" in d and "monitor_id" not in d for d in defs)


async def test_a_backlog_is_worked_off_without_waiting_for_the_poll_interval(
    sessions: Sessions,
) -> None:
    """60 due monitors, at most 5 running at once, a 30 s poll interval: the worker must start the
    next check as soon as a slot frees, not once per poll interval."""
    await make_monitors(sessions, 60)
    worker = Worker(
        sessions,
        policy=SsrfPolicy(),
        resolver=PublicResolver(),
        transport=ok_transport(),
        batch_size=5,
        poll_interval_s=30,
    )
    task = asyncio.create_task(worker.run())
    done = 0
    try:
        for _ in range(100):
            await asyncio.sleep(0.1)
            async with sessions() as s:
                done = await s.scalar(text("SELECT count(*) FROM check_runs")) or 0
            if done == 60:
                break
        assert done == 60
    finally:
        worker.request_stop()
        await asyncio.wait_for(task, 5)
