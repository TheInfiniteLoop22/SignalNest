"""Dev CLI.

python -m app.cli seed [--url URL]   create a dev user and one monitor
python -m app.cli probe URL          run a single probe and print the timings (no database)
"""

import argparse
import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Monitor, User
from app.db.session import SessionLocal
from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import ProbeRequest
from app.security import hash_password


async def seed(url: str, name: str, interval: int, password: str) -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == settings.dev_user_email))
        if user is None:
            user = User(email=settings.dev_user_email, password_hash=hash_password(password))
            session.add(user)
            await session.flush()
        monitor = await session.scalar(
            select(Monitor).where(Monitor.owner_id == user.id, Monitor.name == name)
        )
        if monitor is None:
            monitor = Monitor(owner_id=user.id, name=name, url=url, interval_seconds=interval)
            session.add(monitor)
        await session.commit()
        print(f"user={user.email} monitor={monitor.name} ({monitor.url})")


async def probe(url: str) -> None:
    cfg = get_settings()
    policy = SsrfPolicy.from_settings(cfg.ssrf_allow_private_cidrs, cfg.allowed_ports)
    r = await run_probe(ProbeRequest(url=url), policy=policy)
    print(f"{r.outcome.value} class={r.failure_class} status={r.http_status} ip={r.resolved_ip}")
    print(f"dns={r.dns_ms} connect={r.connect_ms} tls={r.tls_ms} ttfb={r.ttfb_ms} (ms)")
    print(f"total={r.total_ms} ms")
    if r.detail:
        print(f"detail: {r.detail}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--url", default="http://localhost:8001/ok")
    p_seed.add_argument("--name", default="dev-ok")
    p_seed.add_argument("--interval", type=int, default=60)
    p_seed.add_argument("--password", default="dev-password-123")
    p_probe = sub.add_parser("probe")
    p_probe.add_argument("url")
    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(seed(args.url, args.name, args.interval, args.password))
    else:
        asyncio.run(probe(args.url))


if __name__ == "__main__":
    main()
