"""The Postgres queue and the incident lifecycle, against a real database."""

import asyncio
import ipaddress
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Monitor, User
from app.probe.ssrf import SsrfPolicy
from app.probe.types import FailureClass, Outcome, ProbeResult
from app.worker.loop import Worker
from app.worker.queue import claim_due, record_result

pytestmark = pytest.mark.integration

LOCAL = SsrfPolicy(allowed_ports=None, allow_private_cidrs=(ipaddress.ip_network("127.0.0.0/8"),))

Sessions = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def sessions(migrated_db: str) -> AsyncIterator[Sessions]:
    engine = create_async_engine(migrated_db, pool_size=20)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    await engine.dispose()


async def make_monitors(
    sessions: Sessions, n: int = 1, *, url: str = "http://faultline.test/ok", threshold: int = 3
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


async def cycle(sessions: Sessions, monitor_id: uuid.UUID, res: ProbeResult) -> None:
    """Make the monitor due, claim it, and record `res`: one full scheduled run."""
    async with sessions() as s:
        await s.execute(
            text("UPDATE monitors SET next_run_at = now() - interval '1 second' WHERE id = :id"),
            {"id": monitor_id},
        )
        await s.commit()
    async with sessions() as s:
        claims = await claim_due(s, limit=10)
    claim = next(c for c in claims if c.id == monitor_id)
    async with sessions() as s:
        await record_result(s, claim, res, started_at=datetime.now(UTC))


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


# --- claiming ---------------------------------------------------------------------------


async def test_a_due_monitor_is_claimed_once_while_leased(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions)
    async with sessions() as s:
        first = await claim_due(s, limit=10)
    async with sessions() as s:
        second = await claim_due(s, limit=10)
    assert [c.id for c in first] == [mid]
    assert second == []  # the lease hides it from every other worker


async def test_an_expired_lease_is_reclaimed(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions)
    async with sessions() as s:
        await claim_due(s, limit=10)  # a worker takes it, then "dies"
        await s.execute(text("UPDATE monitors SET locked_until = now() - interval '1 second'"))
        await s.commit()
    async with sessions() as s:
        assert [c.id for c in await claim_due(s, limit=10)] == [mid]


async def test_not_yet_due_monitors_are_not_claimed(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions)
    async with sessions() as s:
        await s.execute(
            text("UPDATE monitors SET next_run_at = now() + interval '1 hour' WHERE id = :id"),
            {"id": mid},
        )
        await s.commit()
    async with sessions() as s:
        assert await claim_due(s, limit=10) == []


async def test_concurrent_workers_never_claim_the_same_monitor(sessions: Sessions) -> None:
    await make_monitors(sessions, 40)

    async def claim_all() -> list[uuid.UUID]:
        async with sessions() as s:
            return [c.id for c in await claim_due(s, limit=40)]

    batches = await asyncio.gather(*(claim_all() for _ in range(5)))
    claimed = [mid for batch in batches for mid in batch]
    assert len(claimed) == 40
    assert len(set(claimed)) == 40  # SKIP LOCKED: each monitor went to exactly one worker


# --- recording and incidents ------------------------------------------------------------


async def test_recording_stores_the_run_and_schedules_the_next_one(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions)
    await cycle(sessions, mid, result(True))
    row = await monitor_row(sessions, mid)
    assert row["status"] == "UP"
    assert row["locked_until"] is None
    assert row["last_checked_at"] is not None
    assert row["next_run_at"] > datetime.now(UTC) + timedelta(seconds=20)  # now + interval
    async with sessions() as s:
        assert await s.scalar(text("SELECT count(*) FROM check_runs")) == 1


async def test_failures_below_the_threshold_open_no_incident(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, threshold=3)
    await cycle(sessions, mid, result(True))
    await cycle(sessions, mid, result(False))
    await cycle(sessions, mid, result(False))
    assert (await monitor_row(sessions, mid))["status"] == "UP"
    assert await incidents(sessions, mid) == []


async def test_outage_opens_one_incident_and_recovery_resolves_it(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, threshold=3)
    await cycle(sessions, mid, result(True))
    for _ in range(5):  # two more failures than the threshold: still a single incident
        await cycle(sessions, mid, result(False, FailureClass.TIMEOUT))

    (inc,) = await incidents(sessions, mid)
    assert inc["resolved_at"] is None
    assert inc["failure_class"] == "TIMEOUT"
    assert (await monitor_row(sessions, mid))["status"] == "DOWN"
    async with sessions() as s:
        first_failure = await s.scalar(
            text(
                "SELECT started_at FROM check_runs WHERE outcome = 'FAIL' "
                "ORDER BY started_at LIMIT 1 OFFSET 2"
            )
        )
    # Backdated to the first failure of the streak that crossed the threshold.
    assert inc["opened_at"] <= first_failure

    await cycle(sessions, mid, result(True))
    (inc,) = await incidents(sessions, mid)
    assert inc["resolved_at"] is not None
    assert (await monitor_row(sessions, mid))["status"] == "UP"


async def test_a_second_outage_is_a_second_incident(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions, threshold=1)
    for ok in (False, True, False, True):
        await cycle(sessions, mid, result(ok))
    assert len(await incidents(sessions, mid)) == 2


async def test_recording_for_a_deleted_monitor_is_a_no_op(sessions: Sessions) -> None:
    (mid,) = await make_monitors(sessions)
    async with sessions() as s:
        (claim,) = await claim_due(s, limit=1)
    async with sessions() as s:
        await s.execute(text("DELETE FROM monitors WHERE id = :id"), {"id": mid})
        await s.commit()
    async with sessions() as s:
        assert await record_result(s, claim, result(True), started_at=datetime.now(UTC)) is False


# --- the whole loop, against Faultline --------------------------------------------------


async def test_worker_probes_a_real_endpoint_and_detects_an_outage(
    sessions: Sessions, faultline: str
) -> None:
    (ok_id,) = await make_monitors(sessions, url=f"{faultline}/ok")
    (bad_id,) = await make_monitors(sessions, url=f"{faultline}/status/503", threshold=1)

    worker = Worker(sessions, policy=LOCAL, poll_interval_s=0.05, shutdown_grace_s=2)
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
