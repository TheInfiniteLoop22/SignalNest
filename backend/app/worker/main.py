"""Worker entrypoint: `python -m app.worker.main`. Exits 0 after a graceful SIGTERM/SIGINT."""

import asyncio
import signal

from prometheus_client import start_http_server

from app.config import get_settings
from app.db.session import SessionLocal
from app.observability import metrics
from app.observability.logging import setup_logging
from app.services.probe_once import policy_from_settings
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
        policy=policy_from_settings(),
        worker_id=cfg.worker_id or None,
        max_concurrency=cfg.max_concurrency,
        poll_interval_s=cfg.poll_interval_ms / 1000,
        claim_batch_max=cfg.claim_batch_max,
        shutdown_grace_s=cfg.shutdown_grace_s,
        heartbeat_interval_s=cfg.heartbeat_interval_s,
        retention_every_s=cfg.retention_interval_s,
        retention_days=cfg.run_retention_days,
    )
    if cfg.metrics_port:
        # Internal-only listener (compose does not publish it); the API serves its own.
        start_http_server(cfg.metrics_port, registry=metrics.registry)
    _install_signal_handlers(worker, asyncio.get_running_loop())
    await worker.run()


def main() -> None:
    setup_logging("worker", get_settings().log_level)
    asyncio.run(amain())


if __name__ == "__main__":
    main()
