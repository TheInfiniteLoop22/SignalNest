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

### R1 — "No new runs for several minutes"
1. System page: any worker with `last_seen_at` < 30 s ago? If none → `docker compose ps worker`, `docker compose logs --tail=200 worker`.
2. Worker alive but lag rising → check `in_flight_probes` vs `MAX_CONCURRENCY` (saturated by hanging targets?) and `db_errors_total`.
3. Leases stuck? `SELECT monitor_id, lease_owner, lease_expires_at FROM monitor_state WHERE lease_owner IS NOT NULL ORDER BY lease_expires_at;` — expired leases should be reclaimed automatically; if not, the claim query is broken.
4. Are all monitors paused? `SELECT count(*) FILTER (WHERE is_paused) FROM monitors;`

### R2 — "Everything is failing at once"
1. Failure class distribution in the last 10 min. All `DNS_*` or `CONNECT_TIMEOUT` across unrelated hosts → likely **our** network/DNS, not targets.
2. From the worker container: `python -m app.cli probe-url https://example.com` (or the canary, next iteration).
3. If ours: mark the period in `LEARNING_LOG`; next iteration's canary automates `INCONCLUSIVE` marking.

### R3 — "Database unavailable"
1. `/readyz` 503; worker logs `db.unavailable` with backoff.
2. `docker compose logs db`; disk space (`df -h`) — the most common cause on small VMs.
3. After recovery: confirm runs resume and no duplicates: `SELECT monitor_id, scheduled_for, count(*) FROM check_runs GROUP BY 1,2 HAVING count(*)>1;` (should be empty — the unique constraint makes it impossible).

### R4 — "Disk filling up"
1. `SELECT pg_size_pretty(pg_total_relation_size('check_runs'));`
2. Check `job_runs` for retention failures; run `python -m app.cli retention --dry-run`.
3. Lower `RUN_RETENTION_DAYS` or add rollups (next iteration).

### R5 — "Suspected SSRF attempt"
1. Search logs for `BLOCKED_BY_POLICY`; group by user.
2. Disable the account (set sessions revoked), keep evidence, review whether any blocked request reached the network (it shouldn't: the guard runs before connect).

### R6 — Deploy & rollback
Deploy: pull new image tag → `alembic upgrade head` (migrations must be backward-compatible with the previous release: expand → migrate → contract) → restart worker then api. Rollback: previous image tag; downgrade migrations only if the release added no data you need.
