"""Dev CLI: `python -m app.cli seed [--url URL]` and `python -m app.cli probe <monitor_id>`."""

import argparse
import asyncio
import uuid

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Monitor, MonitorState, User
from app.db.session import SessionLocal
from app.services.probe_once import probe_monitor


async def seed(url: str) -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == settings.dev_user_email))
        if user is None:
            user = User(email=settings.dev_user_email)
            session.add(user)
            await session.flush()
        monitor = await session.scalar(
            select(Monitor).where(Monitor.owner_id == user.id, Monitor.name == "dev-ok")
        )
        if monitor is None:
            monitor = Monitor(owner_id=user.id, name="dev-ok", url=url)
            session.add(monitor)
            await session.flush()
            session.add(MonitorState(monitor_id=monitor.id))
        await session.commit()
        print(f"monitor_id={monitor.id}")


async def probe(monitor_id: uuid.UUID) -> None:
    async with httpx.AsyncClient() as client, SessionLocal() as session:
        run = await probe_monitor(
            session, monitor_id, client=client, worker_id=get_settings().worker_id
        )
        print(
            f"run={run.id} outcome={run.outcome} status={run.http_status} "
            f"total_ms={run.total_ms} class={run.failure_class}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--url", default="http://localhost:8001/ok")
    p_probe = sub.add_parser("probe")
    p_probe.add_argument("monitor_id", type=uuid.UUID)
    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(seed(args.url))
    else:
        asyncio.run(probe(args.monitor_id))


if __name__ == "__main__":
    main()
