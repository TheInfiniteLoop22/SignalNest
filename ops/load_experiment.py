"""Load experiment: N monitors x interval against Faultline on K worker processes.

    PYTHONPATH=backend python ops/load_experiment.py --monitors 200 --interval 30 --duration 90

Needs Postgres reachable via DATABASE_URL (docker compose up -d db) and applied migrations.
It starts Faultline and --workers worker processes as subprocesses on this machine, seeds N monitors for a
throwaway user, lets the worker run for `--duration` seconds, then reports:

  * dispatch delay  = check_runs.started_at - scheduled_for  (how late a probe actually began)
  * scheduler lag   = worker gauge, sampled once a second (age of the oldest due monitor)
  * completeness    = runs recorded vs slots expected, duplicates
  * probe latency   = total_ms of the recorded runs

Everything (database, Faultline, worker, this script) shares one machine, so numbers describe
*this setup*, not a deployment. Record the machine and the command next to any number you quote.
"""

import argparse
import asyncio
import os
import platform
import statistics
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.security import hash_password

ROOT = Path(__file__).resolve().parents[1]
FAULTLINE_PORT = 8001
METRICS_PORT = 9100


def start(args: list[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603
        args, env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def scrape(name: str) -> float | None:
    try:
        body = (
            urllib.request.urlopen(  # noqa: S310
                f"http://127.0.0.1:{METRICS_PORT}/metrics", timeout=2
            )
            .read()
            .decode()
        )
    except OSError:
        return None
    for line in body.splitlines():
        if line.startswith(name):
            return float(line.rsplit(" ", 1)[1])
    return None


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--monitors", type=int, default=200)
    p.add_argument("--interval", type=int, default=30)
    p.add_argument("--duration", type=int, default=90)
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--workers", type=int, default=1, help="worker processes (horizontal scaling)")
    a = p.parse_args()

    engine = create_async_engine(get_settings().database_url)
    email = f"load-{uuid.uuid4().hex[:8]}@experiment.local"
    async with engine.begin() as c:
        uid = await c.scalar(
            text("INSERT INTO users (email, password_hash) VALUES (:e, :h) RETURNING id"),
            {"e": email, "h": hash_password("load-experiment-pw")},
        )
        await c.execute(
            text(
                """
                WITH m AS (
                    INSERT INTO monitors (owner_id, name, url, interval_seconds, timeout_ms)
                    SELECT :u, 'load-' || g, :url, :iv, 5000 FROM generate_series(1, :n) g
                    RETURNING id)
                INSERT INTO monitor_state (monitor_id, next_run_at)
                SELECT id, now() + random() * make_interval(secs => :iv) FROM m
                """
            ),
            {"u": uid, "url": f"http://127.0.0.1:{FAULTLINE_PORT}/ok", "iv": a.interval,
             "n": a.monitors},
        )  # fmt: skip

    env = {
        **os.environ,
        "PYTHONPATH": f"{ROOT / 'backend'}{os.pathsep}{ROOT}",
        "SSRF_ALLOW_PRIVATE_CIDRS": "127.0.0.0/8",
        "ALLOWED_PORTS": "1-65535",
        "MAX_CONCURRENCY": str(a.concurrency),
        "HEARTBEAT_INTERVAL_S": "2",
        "WORKER_ID": "load-worker",
        "LOG_LEVEL": "WARNING",
        "METRICS_PORT": str(METRICS_PORT),
    }
    fault = start([sys.executable, "-m", "uvicorn", "faultline.app:app", "--port",
                   str(FAULTLINE_PORT), "--log-level", "warning"], env)  # fmt: skip
    await asyncio.sleep(3)
    t_start = time.time()
    workers = [
        start(
            [sys.executable, "-m", "app.worker.main"],
            {
                **env,
                "WORKER_ID": f"load-worker-{i}",
                "METRICS_PORT": str(METRICS_PORT if i == 0 else 0),
            },
        )
        for i in range(a.workers)
    ]

    lag: list[float] = []
    inflight: list[float] = []
    try:
        while time.time() - t_start < a.duration:
            await asyncio.sleep(1)
            v = scrape("signalnest_scheduler_lag_seconds")
            f = scrape("signalnest_in_flight_probes")
            if v is not None:
                lag.append(v)
            if f is not None:
                inflight.append(f)
    finally:
        for w in workers:
            w.terminate()
        for w in workers:
            w.wait(10)
        fault.terminate()
        fault.wait(10)

    async with engine.connect() as c:
        row = (
            (
                await c.execute(
                    text(
                        """
                        SELECT count(*) AS runs,
                               count(DISTINCT (r.monitor_id, r.scheduled_for)) AS slots,
                               count(*) FILTER (WHERE r.outcome = 'PASS') AS passed,
                               percentile_cont(0.5) WITHIN GROUP (ORDER BY
                                   extract(epoch FROM r.started_at - r.scheduled_for) * 1000) AS d50,
                               percentile_cont(0.95) WITHIN GROUP (ORDER BY
                                   extract(epoch FROM r.started_at - r.scheduled_for) * 1000) AS d95,
                               percentile_cont(0.99) WITHIN GROUP (ORDER BY
                                   extract(epoch FROM r.started_at - r.scheduled_for) * 1000) AS d99,
                               max(extract(epoch FROM r.started_at - r.scheduled_for) * 1000) AS dmax,
                               percentile_cont(0.5) WITHIN GROUP (ORDER BY r.total_ms) AS p50,
                               percentile_cont(0.95) WITHIN GROUP (ORDER BY r.total_ms) AS p95
                        FROM check_runs r JOIN monitors m ON m.id = r.monitor_id
                        WHERE m.owner_id = :u
                        """
                    ),
                    {"u": uid},
                )
            )
            .mappings()
            .one()
        )
    async with engine.begin() as c:
        await c.execute(text("DELETE FROM users WHERE id = :u"), {"u": uid})
    await engine.dispose()

    elapsed = a.duration
    expected = a.monitors * elapsed / a.interval
    print(
        f"machine: {platform.platform()} / python {platform.python_version()}, cpus={os.cpu_count()}"
    )
    print(f"command: {' '.join(sys.argv)}")
    print(f"monitors={a.monitors} interval={a.interval}s duration={elapsed}s "
          f"workers={a.workers} max_concurrency={a.concurrency} target_rate={a.monitors / a.interval:.1f} probes/s")  # fmt: skip
    print(
        f"runs recorded      : {row['runs']} (expected ~{expected:.0f}; "
        f"{100 * row['runs'] / expected:.0f}%)"
    )
    print(f"distinct slots     : {row['slots']}  duplicates={row['runs'] - row['slots']}")
    print(f"outcome PASS       : {row['passed']} / {row['runs']}")
    print(f"dispatch delay ms  : p50={row['d50']:.0f} p95={row['d95']:.0f} "
          f"p99={row['d99']:.0f} max={row['dmax']:.0f}")  # fmt: skip
    print(f"probe total_ms     : p50={row['p50']:.0f} p95={row['p95']:.0f}")
    if lag:
        print(f"scheduler lag s    : mean={statistics.mean(lag):.2f} max={max(lag):.2f} "
              f"(n={len(lag)} samples)")  # fmt: skip
    if inflight:
        print(f"in-flight probes   : mean={statistics.mean(inflight):.1f} max={max(inflight):.0f}")


if __name__ == "__main__":
    asyncio.run(main())
