"""AC7 / AC9 / AC10-style scenario tests: Faultline + real worker pieces + real Postgres."""

import ipaddress
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Monitor, MonitorState, User
from app.incidents.evidence import build_incident_evidence
from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import FailureClass, Outcome
from app.services.probe_once import request_for
from app.worker.claim import claim_due
from app.worker.complete import complete_run

pytestmark = pytest.mark.integration

LOCAL = SsrfPolicy(allowed_ports=None, allow_private_cidrs=(ipaddress.ip_network("127.0.0.0/8"),))


@pytest_asyncio.fixture
async def sessions(migrated_db: str):  # type: ignore[no-untyped-def]
    engine = create_async_engine(migrated_db)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users CASCADE"))
    await engine.dispose()


async def make_monitor(
    sessions: async_sessionmaker[AsyncSession], url: str, **kw: object
) -> uuid.UUID:
    async with sessions() as s:
        u = User(email=f"{uuid.uuid4().hex[:6]}@t.test", password_hash="!")
        s.add(u)
        await s.flush()
        m = Monitor(owner_id=u.id, name="m", url=url, interval_seconds=30, timeout_ms=1000, **kw)
        s.add(m)
        await s.flush()
        s.add(MonitorState(monitor_id=m.id, next_run_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
        return m.id


class Cycle:
    """Runs one full claim -> probe -> complete cycle for a monitor, deterministically."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], mid: uuid.UUID) -> None:
        self.sessions, self.mid, self.n = sessions, mid, 0

    async def __call__(self) -> Outcome:
        self.n += 1
        async with self.sessions() as s:
            await s.execute(
                text(
                    "UPDATE monitor_state SET next_run_at = now() - interval '1 second' "
                    "WHERE monitor_id = :m"
                ),
                {"m": self.mid},
            )
            await s.commit()
        async with self.sessions() as s:
            [claim] = await claim_due(s, worker_id="w", limit=1)
            monitor = await s.get(Monitor, self.mid)
            assert monitor is not None
            req = request_for(monitor)
        started = datetime.now(UTC)
        result = await run_probe(req, policy=LOCAL)
        async with self.sessions() as s:
            await complete_run(
                s, claim, result, worker_id="w", started_at=started,
                finished_at=datetime.now(UTC), jitter_s=0,
            )  # fmt: skip
        return result.outcome


async def set_mode(base: str, mode: str) -> None:
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{base}/scenario", json={"mode": mode})
        assert r.status_code == 200


async def incidents(sessions: async_sessionmaker[AsyncSession]) -> list[dict[str, object]]:
    async with sessions() as s:
        rows = (
            (await s.execute(text("SELECT * FROM incidents ORDER BY opened_at"))).mappings().all()
        )
        return [dict(r) for r in rows]


async def events(sessions: async_sessionmaker[AsyncSession], iid: uuid.UUID) -> list[str]:
    async with sessions() as s:
        rows = await s.execute(
            text("SELECT kind FROM incident_events WHERE incident_id=:i ORDER BY id"), {"i": iid}
        )
        return [r[0] for r in rows]


async def test_healthy_outage_healthy_yields_exactly_one_incident_ac7(
    faultline: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    mid = await make_monitor(sessions, f"{faultline}/api/products")
    cycle = Cycle(sessions, mid)

    await set_mode(faultline, "healthy")
    assert await cycle() is Outcome.PASS
    assert await cycle() is Outcome.PASS
    assert await incidents(sessions) == []

    await set_mode(faultline, "outage")
    assert await cycle() is Outcome.FAIL  # failure 1
    assert await cycle() is Outcome.FAIL  # failure 2
    assert await incidents(sessions) == []  # threshold is 3
    assert await cycle() is Outcome.FAIL  # failure 3 -> OPEN
    [inc] = await incidents(sessions)
    assert inc["status"] == "OPEN"
    assert inc["failure_count"] == 3
    assert inc["dominant_failure_class"] == "HTTP_UNEXPECTED_STATUS"

    # opened_at is the START of the first failing run, not the run that crossed the threshold
    async with sessions() as s:
        first = (
            await s.execute(
                text(
                    "SELECT id, started_at FROM check_runs WHERE outcome='FAIL' ORDER BY id LIMIT 1"
                )
            )
        ).one()
    assert inc["first_failure_run_id"] == first.id
    assert inc["opened_at"] == first.started_at

    assert await cycle() is Outcome.FAIL  # still the same incident
    await set_mode(faultline, "healthy")
    assert await cycle() is Outcome.PASS  # recovering (1 of 2)
    open_now = await incidents(sessions)
    assert len(open_now) == 1 and open_now[0]["status"] == "OPEN"
    assert await cycle() is Outcome.PASS  # resolved

    [inc] = await incidents(sessions)
    assert inc["status"] == "RESOLVED"
    assert inc["failure_count"] == 4
    assert inc["resolved_at"] is not None
    assert inc["resolving_run_id"] is not None
    assert await events(sessions, inc["id"]) == [  # type: ignore[arg-type]
        "OPENED",
        "RECOVERING",
        "RESOLVED",
    ]

    async with sessions() as s:
        st = await s.get(MonitorState, mid)
        assert st is not None
        assert (st.health, st.incident_open, st.consecutive_failures) == ("UP", False, 0)

    # A second outage opens a second, separate incident.
    await set_mode(faultline, "outage")
    for _ in range(3):
        await cycle()
    assert len(await incidents(sessions)) == 2


async def test_evidence_and_hints_for_an_http_outage(
    faultline: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    mid = await make_monitor(sessions, f"{faultline}/api/products")
    cycle = Cycle(sessions, mid)
    await set_mode(faultline, "outage")
    for _ in range(3):
        await cycle()
    [inc] = await incidents(sessions)
    async with sessions() as s:
        ev = await build_incident_evidence(s, inc["id"])  # type: ignore[arg-type]
    assert ev is not None
    assert ev["class_counts"] == {"HTTP_UNEXPECTED_STATUS": 3}
    assert ev["first_failure_run"]["http_status"] == 503
    assert [h["code"] for h in ev["hints"]] == ["H_5XX"]
    assert len(ev["hints"][0]["evidence_run_ids"]) == 3
    json.dumps(ev)  # must be JSON-serialisable for the API


async def test_broken_contract_scenario_gives_contract_hint_ac10(
    faultline: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    assertions = [{"type": "json_equals", "path": "items.0.name", "value": "widget"}]
    mid = await make_monitor(sessions, f"{faultline}/api/products", assertions=assertions)
    cycle = Cycle(sessions, mid)
    await set_mode(faultline, "healthy")
    assert await cycle() is Outcome.PASS
    await set_mode(faultline, "broken_contract")
    for _ in range(3):
        assert await cycle() is Outcome.FAIL
    [inc] = await incidents(sessions)
    assert inc["dominant_failure_class"] == FailureClass.ASSERTION_FAILED
    async with sessions() as s:
        ev = await build_incident_evidence(s, inc["id"])  # type: ignore[arg-type]
    assert ev is not None
    [hint] = ev["hints"]
    assert hint["code"] == "H_CONTRACT"
    assert "json_equals" in hint["text"]


async def test_inconclusive_runs_never_open_incidents_ac9(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # Everything resolves to a blocked address under the default policy -> INCONCLUSIVE.
    mid = await make_monitor(sessions, "http://127.0.0.1:9/")
    for _ in range(5):
        async with sessions() as s:
            await s.execute(text("UPDATE monitor_state SET next_run_at = now() - interval '1 s'"))
            await s.commit()
        async with sessions() as s:
            [claim] = await claim_due(s, worker_id="w", limit=1)
            monitor = await s.get(Monitor, mid)
            assert monitor is not None
            req = request_for(monitor)
        result = await run_probe(req, policy=SsrfPolicy())
        assert result.outcome is Outcome.INCONCLUSIVE
        t = datetime.now(UTC)
        async with sessions() as s:
            await complete_run(s, claim, result, worker_id="w", started_at=t, finished_at=t)
    assert await incidents(sessions) == []
    async with sessions() as s:
        st = await s.get(MonitorState, mid)
        assert st is not None
        assert (st.health, st.consecutive_failures) == ("UNKNOWN", 0)


async def test_database_rejects_a_second_open_incident_for_a_monitor(
    faultline: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    mid = await make_monitor(sessions, f"{faultline}/api/products")
    await set_mode(faultline, "outage")
    cycle = Cycle(sessions, mid)
    for _ in range(3):
        await cycle()
    async with sessions() as s:
        run_id = await s.scalar(text("SELECT id FROM check_runs LIMIT 1"))
        with pytest.raises(IntegrityError):
            await s.execute(
                text(
                    """
                    INSERT INTO incidents (monitor_id, status, opened_at, first_failure_run_id,
                                           failure_count, dominant_failure_class)
                    VALUES (:m, 'OPEN', now(), :r, 1, 'READ_TIMEOUT')
                    """
                ),
                {"m": mid, "r": run_id},
            )
            await s.commit()
