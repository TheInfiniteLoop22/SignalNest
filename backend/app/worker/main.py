"""Worker entrypoint: `python -m app.worker.main`. Exits 0 after a graceful SIGTERM/SIGINT."""

import asyncio
import logging
import signal

from app.config import get_settings
from app.db.session import SessionLocal
from app.probe.ssrf import SsrfPolicy
from app.worker.loop import Worker


def _install_signal_handlers(worker: Worker, loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:  # Windows event loops
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(worker.request_stop))


async def amain() -> None:
    cfg = get_settings()
    worker = Worker(
        SessionLocal,
        policy=SsrfPolicy.from_settings(
            cfg.allowed_ports, cfg.ssrf_allow_private_cidrs, cfg.allowed_hosts
        ),
        max_concurrency=cfg.max_concurrency,
        poll_interval_s=cfg.poll_interval_ms / 1000,
        claim_batch_max=cfg.claim_batch_max,
        shutdown_grace_s=cfg.shutdown_grace_s,
        alert_max_attempts=cfg.alert_max_attempts,
        retention_days=cfg.retention_days,
        maintenance_interval_s=cfg.maintenance_interval_s,
    )
    _install_signal_handlers(worker, asyncio.get_running_loop())
    await worker.run()


def main() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(amain())


if __name__ == "__main__":
    main()
