"""Dev CLI: `python -m app.cli seed [--url URL]` and `python -m app.cli probe <monitor_id>`."""

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Monitor, MonitorState, User
from app.db.session import SessionLocal
from app.probe.engine import run_probe
from app.probe.types import ProbeRequest, ProbeResult
from app.security import hash_password
from app.services.probe_once import policy_from_settings, probe_monitor
from app.worker import retention


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
    _print_attempts(result)


def _ms(v: int | None) -> str:
    return "-" if v is None else str(v)


def _print_attempts(result: ProbeResult) -> None:
    print(f"outcome={result.outcome} class={result.failure_class}")
    print(f"{'attempt':<8}{'ip':<16}{'dns':>6}{'conn':>6}{'tls':>6}{'ttfb':>6}{'total':>7}  result")
    for i, a in enumerate(result.attempts, 1):
        outcome = a.error_class or f"HTTP {a.http_status}"
        print(
            f"{i:<8}{a.resolved_ip or '-':<16}{_ms(a.dns_ms):>6}{_ms(a.connect_ms):>6}"
            f"{_ms(a.tls_ms):>6}{_ms(a.ttfb_ms):>6}{a.total_ms:>7}  {outcome}"
        )
    for r in result.assertion_results:
        verdict = "ok" if r.passed else "FAIL"
        print(f"assert {r.type}: {verdict} expected={r.expected} actual={r.actual}")


async def probe_url(url: str) -> None:
    """Probe an arbitrary URL with this host's resolver/network and the real SSRF policy.

    Runbook R2: tells you whether failures are the targets' or ours.
    """
    result = await run_probe(
        ProbeRequest(url=url, timeout_ms=5000), policy=policy_from_settings(), retry_delay_s=0
    )
    _print_attempts(result)


async def run_retention_cmd(dry_run: bool) -> None:
    from datetime import UTC, datetime

    cfg = get_settings()
    now = datetime.now(UTC)
    if dry_run:
        async with SessionLocal() as session:
            n = await retention.preview(session, retention_days=cfg.run_retention_days, now=now)
        print(f"would delete {n} runs older than {cfg.run_retention_days} days (dry run)")
        return
    result = await retention.run_retention(
        SessionLocal, retention_days=cfg.run_retention_days, now=now
    )
    print(f"deleted runs={result.runs} sessions={result.sessions} heartbeats={result.heartbeats}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--url", default="http://localhost:8001/ok")
    p_seed.add_argument("--name", default="dev-ok")
    p_seed.add_argument("--interval", type=int, default=60)
    p_seed.add_argument("--password", default="dev-password-123")
    p_url = sub.add_parser("probe-url")
    p_url.add_argument("url")
    p_ret = sub.add_parser("retention")
    p_ret.add_argument("--dry-run", action="store_true")
    p_probe = sub.add_parser("probe")
    p_probe.add_argument("monitor_id", type=uuid.UUID)
    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(seed(args.url, args.name, args.interval, args.password))
    elif args.cmd == "probe-url":
        asyncio.run(probe_url(args.url))
    elif args.cmd == "retention":
        asyncio.run(run_retention_cmd(args.dry_run))
    else:
        asyncio.run(probe(args.monitor_id))


if __name__ == "__main__":
    main()
