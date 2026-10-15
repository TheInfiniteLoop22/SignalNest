"""Dev CLI: `python -m app.cli seed [--url URL]` and `python -m app.cli probe <monitor_id>`."""

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Monitor, MonitorState, User
from app.db.session import SessionLocal
from app.services.probe_once import probe_monitor


async def seed(url: str, name: str, interval: int) -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == settings.dev_user_email))
        if user is None:
            user = User(email=settings.dev_user_email)
            session.add(user)
            await session.flush()
        monitor = await session.scalar(
            select(Monitor).where(Monitor.owner_id == user.id, Monitor.name == name)
        )
        if monitor is None:
            monitor = Monitor(owner_id=user.id, name=name, url=url, interval_seconds=interval)
            session.add(monitor)
            await session.flush()
            session.add(MonitorState(monitor_id=monitor.id))
        await session.commit()
        print(f"monitor_id={monitor.id}")


async def probe(monitor_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        run, result = await probe_monitor(
            session, monitor_id, worker_id=get_settings().worker_id or "cli"
        )
    print(f"run={run.id} outcome={result.outcome} class={result.failure_class}")
    print(f"{'attempt':<8}{'ip':<16}{'dns':>6}{'conn':>6}{'tls':>6}{'ttfb':>6}{'total':>7}  result")
    for i, a in enumerate(result.attempts, 1):

        def ms(v: int | None) -> str:
            return "-" if v is None else str(v)

        outcome = a.error_class or f"HTTP {a.http_status}"
        print(
            f"{i:<8}{a.resolved_ip or '-':<16}{ms(a.dns_ms):>6}{ms(a.connect_ms):>6}"
            f"{ms(a.tls_ms):>6}{ms(a.ttfb_ms):>6}{a.total_ms:>7}  {outcome}"
        )
    for r in result.assertion_results:
        verdict = "ok" if r.passed else "FAIL"
        print(f"assert {r.type}: {verdict} expected={r.expected} actual={r.actual}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--url", default="http://localhost:8001/ok")
    p_seed.add_argument("--name", default="dev-ok")
    p_seed.add_argument("--interval", type=int, default=60)
    p_probe = sub.add_parser("probe")
    p_probe.add_argument("monitor_id", type=uuid.UUID)
    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(seed(args.url, args.name, args.interval))
    else:
        asyncio.run(probe(args.monitor_id))


if __name__ == "__main__":
    main()
