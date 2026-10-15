import asyncio
import ipaddress
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Monitor, MonitorState, User
from app.probe.ssrf import SsrfPolicy
from app.probe.types import (
    AttemptResult,
    FailureClass,
    Outcome,
    ProbeResult,
)
from app.worker.claim import claim_due, release_leases
from app.worker.complete import Completion, complete_run
from app.worker.loop import Worker

pytestmark = pytest.mark.integration

LOCAL = SsrfPolicy(allowed_ports=None, allow_private_cidrs=(ipaddress.ip_network("127.0.0.0/8"),))
BACKEND = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def sessions(migrated_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_db, pool_size=20)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE users, monitors, monitor_state, check_runs, worker_heartbeats CASCADE")
        )
    await engine.dispose()


MakeMonitors = Callable[..., Awaitable[list[uuid.UUID]]]


@pytest_asyncio.fixture
async def make_monitors(sessions: async_sessionmaker[AsyncSession]) -> MakeMonitors:
    async def make(
        n: int = 1,
        *,
        url: str = "http://faultline.test/ok",
        interval: int = 30,
        timeout_ms: int = 2000,
        due_in_s: float = -1,
        paused: bool = False,
    ) -> list[uuid.UUID]:
        async with sessions() as s:
            user = await s.scalar(
                text("SELECT id FROM users WHERE email = :e"), {"e": get_settings().dev_user_email}
            )
            if user is None:
                u = User(email=get_settings().dev_user_email)
                s.add(u)
                await s.flush()
                user = u.id
            ids = []
            for i in range(n):
                m = Monitor(
                    owner_id=user,
                    name=f"m-{uuid.uuid4().hex[:8]}-{i}",
                    url=url,
                    interval_seconds=interval,
                    timeout_ms=timeout_ms,
                    is_paused=paused,
                )
                s.add(m)
                await s.flush()
                s.add(
                    MonitorState(
                        monitor_id=m.id,
                        next_run_at=datetime.now(UTC) + timedelta(seconds=due_in_s),
                    )
                )
                ids.append(m.id)
            await s.commit()
            return ids

    return make


def attempt_error(t: datetime, fc: FailureClass) -> AttemptResult:
    return AttemptResult(t, None, None, None, None, None, 1, None, fc, "x")


def fake_result(outcome: Outcome = Outcome.PASS) -> ProbeResult:
    now = datetime.now(UTC)
    fc = None if outcome in (Outcome.PASS, Outcome.DEGRADED) else FailureClass.READ_TIMEOUT
    return ProbeResult(
        outcome=outcome,
        failure_class=fc,
        attempts=[
            AttemptResult(now, "93.184.216.34", 1, 2, None, 3, 5, 200, fc, None),
        ],
        assertion_results=[],
        response_headers=None,
        response_snippet=None,
    )


async def run_count(sessions: async_sessionmaker[AsyncSession], monitor_id: uuid.UUID) -> int:
    async with sessions() as s:
        n = await s.scalar(
            text("SELECT count(*) FROM check_runs WHERE monitor_id = :m"), {"m": monitor_id}
        )
        return int(n or 0)


# --- claim ---------------------------------------------------------------------------------


async def test_concurrent_claims_never_return_the_same_monitor(
    sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    ids = await make_monitors(40)

    async def claim(worker: str) -> list[uuid.UUID]:
        async with sessions() as s:
            return [c.monitor_id for c in await claim_due(s, worker_id=worker, limit=40)]

    results = await asyncio.gather(*(claim(f"w{i}") for i in range(4)))
    flat = [m for r in results for m in r]
    assert len(flat) == len(set(flat)) == 40  # no duplicates, nothing lost
    assert set(flat) == set(ids)


async def test_leased_monitor_is_not_claimed_again_until_lease_expires(
    sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    [mid] = await make_monitors(1, timeout_ms=2000)  # lease = 2*2 + 15 = 19 s
    async with sessions() as s:
        assert len(await claim_due(s, worker_id="a", limit=5)) == 1
    async with sessions() as s:
        assert await claim_due(s, worker_id="b", limit=5) == []
    later = datetime.now(UTC) + timedelta(seconds=20)
    async with sessions() as s:
        reclaimed = await claim_due(s, worker_id="b", limit=5, now=later)
    assert [c.monitor_id for c in reclaimed] == [mid]


async def test_paused_and_future_monitors_are_not_claimed(
    sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    await make_monitors(1, paused=True)
    await make_monitors(1, due_in_s=60)
    async with sessions() as s:
        assert await claim_due(s, worker_id="a", limit=10) == []


async def test_claim_respects_limit_and_oldest_first(
    sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    [old] = await make_monitors(1, due_in_s=-100)
    await make_monitors(3, due_in_s=-1)
    async with sessions() as s:
        claims = await claim_due(s, worker_id="a", limit=2)
    assert len(claims) == 2
    assert old in [c.monitor_id for c in claims]


async def test_release_leases_makes_monitor_claimable_immediately(
    sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    [mid] = await make_monitors(1)
    async with sessions() as s:
        await claim_due(s, worker_id="a", limit=1)
    async with sessions() as s:
        assert await release_leases(s, worker_id="a", monitor_ids=[mid]) == 1
    async with sessions() as s:
        assert len(await claim_due(s, worker_id="b", limit=1)) == 1


# --- complete (TX2) ------------------------------------------------------------------------


async def _claim_one(
    sessions: async_sessionmaker[AsyncSession], worker: str, now: datetime | None = None
):  # type: ignore[no-untyped-def]
    async with sessions() as s:
        [c] = await claim_due(s, worker_id=worker, limit=1, now=now)
    return c


async def test_complete_records_run_advances_slot_and_clears_lease(
    sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    [mid] = await make_monitors(1, interval=30)
    claim = await _claim_one(sessions, "a")
    now = datetime.now(UTC)
    async with sessions() as s:
        out = await complete_run(
            s, claim, fake_result(), worker_id="a", started_at=now, finished_at=now,
            now=claim.scheduled_for + timedelta(seconds=1), jitter_s=0,
        )  # fmt: skip
    assert out is Completion.RECORDED
    async with sessions() as s:
        st = await s.get(MonitorState, mid)
        assert st is not None
        assert st.lease_owner is None
        assert st.next_run_at == claim.scheduled_for + timedelta(seconds=30)
        assert st.health == "UP"
        assert st.last_run_id is not None
    assert await run_count(sessions, mid) == 1


async def test_second_completion_of_same_slot_is_a_noop(
    sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    [mid] = await make_monitors(1)
    claim = await _claim_one(sessions, "a")
    now = datetime.now(UTC)
    kw = dict(started_at=now, finished_at=now, jitter_s=0)
    async with sessions() as s:
        first = await complete_run(s, claim, fake_result(), worker_id="a", **kw)  # type: ignore[arg-type]
    # Re-enter as if the same slot were executed again (lease re-acquired).
    async with sessions() as s:
        await s.execute(
            text("UPDATE monitor_state SET lease_owner='a' WHERE monitor_id=:m"), {"m": mid}
        )
        await s.commit()
    async with sessions() as s:
        second = await complete_run(s, claim, fake_result(), worker_id="a", **kw)  # type: ignore[arg-type]
    assert first is Completion.RECORDED
    assert second is Completion.DUPLICATE
    assert await run_count(sessions, mid) == 1


async def test_expired_lease_taken_over_then_original_finishes_late_is_fenced(
    sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    """docs/11 hard case 1: lease expiry while the original worker is still probing."""
    [mid] = await make_monitors(1, interval=30, timeout_ms=1000)
    a = await _claim_one(sessions, "A")
    later = datetime.now(UTC) + timedelta(seconds=60)
    b = await _claim_one(sessions, "B", now=later)  # A's lease expired; B takes the slot
    assert a.scheduled_for == b.scheduled_for

    t = datetime.now(UTC)
    async with sessions() as s:
        out_a = await complete_run(
            s, a, fake_result(), worker_id="A", started_at=t, finished_at=t, jitter_s=0
        )
    # A recorded the evidence but must not touch state that B owns.
    assert out_a is Completion.LEASE_LOST
    async with sessions() as s:
        st = await s.get(MonitorState, mid)
        assert st is not None
        assert st.lease_owner == "B"
        assert st.next_run_at == a.scheduled_for  # not advanced by A

    async with sessions() as s:
        out_b = await complete_run(
            s, b, fake_result(), worker_id="B", started_at=t, finished_at=t, jitter_s=0
        )
    assert out_b is Completion.DUPLICATE  # run row exists; B still advances the slot
    async with sessions() as s:
        st = await s.get(MonitorState, mid)
        assert st is not None
        assert st.lease_owner is None
        assert st.next_run_at > a.scheduled_for
    assert await run_count(sessions, mid) == 1  # exactly one run for the slot


async def test_monitor_deleted_while_probing_is_handled(
    sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    [mid] = await make_monitors(1)
    claim = await _claim_one(sessions, "a")
    async with sessions() as s:
        await s.execute(text("DELETE FROM monitors WHERE id = :m"), {"m": mid})
        await s.commit()
    t = datetime.now(UTC)
    async with sessions() as s:
        out = await complete_run(
            s, claim, fake_result(), worker_id="a", started_at=t, finished_at=t
        )
    assert out is Completion.MONITOR_DELETED


async def test_inconclusive_run_does_not_change_health(
    sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    [mid] = await make_monitors(1)
    async with sessions() as s:
        await s.execute(
            text("UPDATE monitor_state SET health='UP' WHERE monitor_id=:m"), {"m": mid}
        )
        await s.commit()
    claim = await _claim_one(sessions, "a")
    t = datetime.now(UTC)
    inconclusive = ProbeResult(
        Outcome.INCONCLUSIVE,
        FailureClass.PROBE_INTERNAL_ERROR,
        [attempt_error(t, FailureClass.PROBE_INTERNAL_ERROR)],
        [],
        None,
        None,
    )  # fmt: skip
    async with sessions() as s:
        await complete_run(s, claim, inconclusive, worker_id="a", started_at=t, finished_at=t)
    async with sessions() as s:
        st = await s.get(MonitorState, mid)
        assert st is not None
        assert st.health == "UP"


# --- worker loop (real Faultline, real Postgres) --------------------------------------------


def make_worker(sessions: async_sessionmaker[AsyncSession], **kw: object) -> Worker:
    params: dict[str, object] = {
        "policy": LOCAL,
        "poll_interval_s": 0.1,
        "heartbeat_interval_s": 0.2,
        "shutdown_grace_s": 1.0,
    }
    params.update(kw)
    return Worker(sessions, **params)  # type: ignore[arg-type]


async def test_worker_runs_due_monitors_once_per_slot(
    faultline: str, sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    ids = await make_monitors(5, url=f"{faultline}/ok", interval=30)
    worker = make_worker(sessions, worker_id="w1")
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(2.5)
    worker.request_stop()
    await asyncio.wait_for(task, 5)

    async with sessions() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT monitor_id, scheduled_for, outcome FROM check_runs ORDER BY monitor_id"
                )
            )
        ).all()
        hb = await s.scalar(text("SELECT count(*) FROM worker_heartbeats WHERE worker_id='w1'"))
        leased = await s.scalar(
            text("SELECT count(*) FROM monitor_state WHERE lease_owner IS NOT NULL")
        )
    assert {r.monitor_id for r in rows} == set(ids)
    assert len(rows) == 5  # interval 30 s: exactly one run each in 2.5 s
    assert len({(r.monitor_id, r.scheduled_for) for r in rows}) == 5
    assert all(r.outcome == "PASS" for r in rows)
    assert hb == 1
    assert leased == 0


async def test_two_workers_execute_each_slot_exactly_once_ac2(
    faultline: str, sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    await make_monitors(30, url=f"{faultline}/ok", interval=30)
    w1 = make_worker(sessions, worker_id="w1")
    w2 = make_worker(sessions, worker_id="w2")
    tasks = [asyncio.create_task(w.run()) for w in (w1, w2)]
    await asyncio.sleep(3)
    for w in (w1, w2):
        w.request_stop()
    await asyncio.wait_for(asyncio.gather(*tasks), 5)

    async with sessions() as s:
        total = await s.scalar(text("SELECT count(*) FROM check_runs"))
        distinct = await s.scalar(
            text("SELECT count(DISTINCT (monitor_id, scheduled_for)) FROM check_runs")
        )
        workers = (await s.execute(text("SELECT DISTINCT worker_id FROM check_runs"))).all()
    assert total == distinct == 30
    assert len(workers) >= 1


async def test_no_catch_up_storm_after_downtime_ac11(
    faultline: str, sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    [mid] = await make_monitors(1, url=f"{faultline}/ok", interval=30, due_in_s=-600)
    worker = make_worker(sessions, jitter_s=10.0)  # next slot = now + 10 s, outside the window
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(2.5)
    worker.request_stop()
    await asyncio.wait_for(task, 5)
    assert await run_count(sessions, mid) == 1
    async with sessions() as s:
        st = await s.get(MonitorState, mid)
        assert st is not None
        assert st.next_run_at > datetime.now(UTC)  # no backlog of past-due slots


async def test_concurrency_is_bounded(
    faultline: str, sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    await make_monitors(6, url=f"{faultline}/slow?ms=1000", interval=30, timeout_ms=3000)
    worker = make_worker(sessions, max_concurrency=2)
    peak = 0
    task = asyncio.create_task(worker.run())
    for _ in range(25):
        await asyncio.sleep(0.1)
        peak = max(peak, worker.in_flight)
    worker.request_stop()
    await asyncio.wait_for(task, 10)
    assert 1 <= peak <= 2


async def test_graceful_stop_abandons_inflight_and_releases_leases_ac12(
    faultline: str, sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    [mid] = await make_monitors(1, url=f"{faultline}/hang", interval=30, timeout_ms=30000)
    worker = make_worker(sessions, worker_id="w1", shutdown_grace_s=0.5)
    task = asyncio.create_task(worker.run())
    for _ in range(50):
        await asyncio.sleep(0.1)
        if worker.in_flight:
            break
    assert worker.in_flight == 1
    t0 = asyncio.get_running_loop().time()
    worker.request_stop()
    await asyncio.wait_for(task, 5)
    assert asyncio.get_running_loop().time() - t0 < 3

    async with sessions() as s:
        st = await s.get(MonitorState, mid)
        assert st is not None
        assert st.lease_owner is None  # handed back, claimable right away
    assert await run_count(sessions, mid) == 0  # nothing half-recorded


async def test_kill9_leaves_lease_and_another_worker_recovers_ac3(
    faultline: str, sessions: async_sessionmaker[AsyncSession], make_monitors: MakeMonitors
) -> None:
    [mid] = await make_monitors(1, url=f"{faultline}/hang", interval=30, timeout_ms=30000)
    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "WORKER_ID": "victim",
        "POLL_INTERVAL_MS": "100",
        "SSRF_ALLOW_PRIVATE_CIDRS": "127.0.0.0/8",
        "ALLOWED_PORTS": "1-65535",
    }
    proc = subprocess.Popen(  # noqa: ASYNC220 - one short-lived child; not on a hot path
        [sys.executable, "-m", "app.worker.main"], env=env, cwd=BACKEND.parent,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    try:
        owner = None
        for _ in range(100):
            await asyncio.sleep(0.2)
            async with sessions() as s:
                owner = await s.scalar(
                    text("SELECT lease_owner FROM monitor_state WHERE monitor_id=:m"), {"m": mid}
                )
            if owner == "victim":
                break
        assert owner == "victim"
    finally:
        proc.kill()  # TerminateProcess / SIGKILL: no cleanup code runs
        proc.wait(5)

    async with sessions() as s:
        assert (
            await s.scalar(
                text("SELECT lease_owner FROM monitor_state WHERE monitor_id=:m"), {"m": mid}
            )
            == "victim"
        )  # the dead worker's lease is still there
    assert await run_count(sessions, mid) == 0

    # After the lease expires another worker takes over the same slot.
    later = datetime.now(UTC) + timedelta(seconds=2 * 30 + 16)
    async with sessions() as s:
        claims = await claim_due(s, worker_id="rescuer", limit=1, now=later)
    assert [c.monitor_id for c in claims] == [mid]
