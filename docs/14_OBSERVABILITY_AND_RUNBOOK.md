# 14 — Observability & Runbook

## 1. Watching the watcher
A monitoring tool must make its *own* failures visible and must not blame targets for them.

| Signal | Source | Meaning |
|---|---|---|
| Worker heartbeat | `worker_heartbeats.last_seen_at` (every 10 s) | Worker alive and looping |
| Scheduler lag | `now() − min(next_run_at)` of due, unleased monitors | Workers can't keep up or aren't running |
| In-flight probes | gauge | Saturation vs `MAX_CONCURRENCY` |
| `INCONCLUSIVE` rate | `runs_total{outcome="INCONCLUSIVE"}` | Monitor-side problems |
| DB errors | `db_errors_total{op}` | Datastore trouble |
| Retention job | `job_runs` | Housekeeping is happening |

## 2. Logging
JSON lines to stdout. Fields: `ts, level, msg, service (api|worker), request_id, run_id, monitor_id, worker_id, duration_ms, outcome, failure_class, error`. Rules: one line per run completion (INFO), one per claim batch (DEBUG), errors with stack traces (ERROR). Never log cookies, passwords, bodies, or query-string values.

Example:
```json
{"ts":"2026-10-15T10:00:05Z","level":"INFO","service":"worker","msg":"run.completed","run_id":48213,"monitor_id":"…","worker_id":"worker-7f3a","outcome":"FAIL","failure_class":"READ_TIMEOUT","duration_ms":5003,"attempts":1}
```

## 3. Metrics (`/metrics`, Prometheus format)
| Metric | Type | Labels | Note |
|---|---|---|---|
| `signalnest_runs_total` | counter | outcome, failure_class | **no monitor_id** (cardinality) |
| `signalnest_probe_duration_seconds` | histogram | outcome | buckets 0.05…30 s |
| `signalnest_scheduler_lag_seconds` | gauge | — | computed each loop |
| `signalnest_in_flight_probes` | gauge | worker_id | |
| `signalnest_claim_batch_size` | histogram | — | |
| `signalnest_db_errors_total` | counter | op | |
| `signalnest_http_requests_total` / `_duration_seconds` | counter / histogram | route template, method, status | API |

## 4. Health endpoints
- `/healthz` — liveness, no dependencies. Restart the container if this fails.
- `/readyz` — DB reachable. Remove from load balancing if this fails; **don't** restart (restarting won't fix a DB outage).

## 5. Runbook

Conventions: commands run from the repo root; `docker compose` is the dev/prod-compose CLI. The
database is reachable with `docker compose exec db psql -U signalnest`. Every log line is JSON,
so `| jq` works (`docker compose logs --no-log-prefix worker | jq 'select(.level=="ERROR")'`).

### R1 - "No new runs for several minutes"
Look in this order; stop at the first thing that is wrong.
1. **System page** (`/system`) or `GET /api/v1/system/status`: is any worker `alive` (seen < 30 s ago)?
   - None: `docker compose ps worker`, then `docker compose logs --tail=200 worker`. Look for `db.unavailable` (R3), a crash loop, or no `worker.started`.
2. Worker alive but `scheduler.lag_seconds` rising: probes are saturating the worker.
   - `curl -s localhost:9100/metrics | grep -E "in_flight|lag"` from inside the worker container (`docker compose exec worker python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:9100/metrics').read().decode())" | grep signalnest_`).
   - `in_flight` pinned at `MAX_CONCURRENCY` means hanging targets are holding slots (each holds one for up to its timeout). Raise `MAX_CONCURRENCY` or lower the offending monitors' timeouts; add `--scale worker=2`.
3. Stuck leases (should expire by themselves):
   `SELECT monitor_id, lease_owner, lease_expires_at FROM monitor_state WHERE lease_owner IS NOT NULL ORDER BY lease_expires_at;`
   A lease older than its expiry that is never reclaimed means the claim query is failing: check worker logs for `db.unavailable`.
4. Everything paused? `SELECT count(*) FILTER (WHERE is_paused) AS paused, count(*) AS total FROM monitors;`
5. Due but not claimed: `SELECT count(*), min(next_run_at) FROM monitor_state WHERE next_run_at <= now() AND lease_owner IS NULL;`

### R2 - "Everything is failing at once"
1. Class distribution, last 10 minutes:
   `SELECT failure_class, count(*) FROM check_runs WHERE started_at > now() - interval '10 minutes' AND outcome='FAIL' GROUP BY 1 ORDER BY 2 DESC;`
   All `DNS_*` / `CONNECT_TIMEOUT` across unrelated hosts points at **our** network or resolver.
2. From the worker's own network: `docker compose exec worker python -m app.cli probe-url https://example.com` (needs `ALLOWED_PORTS` to include 443 and egress). A failure here is ours.
3. If ours: note the window. Incidents opened during it are real records but not target faults; the next-iteration canary will mark them `INCONCLUSIVE` automatically.

### R3 - "Database unavailable"
1. `curl -i localhost:8000/readyz` returns 503 `{"reason":"database"}`; `/healthz` stays 200 (do not restart the API for this). Worker logs `db.unavailable` with a growing `backoff_s` (1 s -> 30 s).
2. `docker compose logs --tail=100 db` and `df -h` on the host (a full disk is the most common cause on small VMs).
3. After recovery the worker resumes by itself; no restart is needed. Verify no duplicates (should be impossible, the unique constraint prevents it):
   `SELECT monitor_id, scheduled_for, count(*) FROM check_runs GROUP BY 1,2 HAVING count(*) > 1;`
4. In-flight probes during the outage were discarded and their slots re-run after the lease expired: expect a short burst, not a backlog.

### R4 - "Disk filling up"
1. `SELECT pg_size_pretty(pg_total_relation_size('check_runs'));` and `SELECT pg_size_pretty(pg_database_size('signalnest'));`
2. Did retention run? `SELECT * FROM job_runs WHERE job_name='retention' ORDER BY started_at DESC LIMIT 5;` (also on the System page). A non-null `error` explains why not.
3. Preview, then run: `docker compose exec worker python -m app.cli retention --dry-run` then without `--dry-run`.
4. Runs referenced by an incident are kept by design; if those dominate, resolve or delete old incidents.
5. Lower `RUN_RETENTION_DAYS` (default 14) or reduce check frequency. Space is only returned to the OS after `VACUUM FULL` (takes a lock) or a dump/restore; plain deletes make room for reuse.

### R5 - "Suspected SSRF attempt"
1. Blocked probes: `SELECT m.owner_id, count(*) FROM check_runs r JOIN monitors m ON m.id = r.monitor_id WHERE r.failure_class='BLOCKED_BY_POLICY' AND r.started_at > now() - interval '24 hours' GROUP BY 1;`
2. Create-time rejections appear as `http.request` lines with `status:422` on `/api/v1/monitors`; grep for `blocked-target` in the API logs (request ids correlate).
3. The guard runs before any connect, so a blocked target never receives a packet. Revoke the account's sessions: `UPDATE sessions SET revoked_at = now() WHERE user_id = '<id>';` and review the monitor's `url` history.

### R6 - Deploy and rollback
Deploy: pull the new image tag -> run the one-shot `migrate` service (`docker compose run --rm migrate`) -> restart `worker`, then `api`. Migrations must be backward compatible with the previous release (expand -> migrate -> contract). Rollback: previous image tag; run `alembic downgrade` only if the release added no data you need (migration 0003's downgrade is lossy for intervals below 30 s).

### R7 - "A worker died / was killed"
Nothing to do: its leases expire (`2 x timeout + 15 s`) and another worker re-runs the slot. Check the System page shows it as `Stale`; `docker compose up -d worker` to replace it. In-flight probes at the time are re-run, so a monitor may show a single delayed check.
