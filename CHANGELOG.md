# Changelog

All notable changes are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Changed
- Simplified to an explainable core (the full version stays at the `v1.0.0` tag).
- Incidents: open after N consecutive failures, resolve on the first pass. Removed the event
  timeline, evidence builder, diagnosis hints and recovery threshold.
- Probe: one attempt, outcomes PASS/FAIL, 7 failure classes (was 14), checks are `expected_status`
  and `body_contains` (was a JSON assertion engine). Removed degraded state and response storage.
- Worker: claim and record live in `worker/queue.py`; one `locked_until` lease column replaces
  fencing, lease owner and slot idempotency; next run is `now() + interval`.
- Schema: 5 tables (scheduler state merged into `monitors`); the 7 migrations are squashed into one.
- API: removed pause/resume, check-now, run detail, stats and cursor pagination.
- Frontend: 5 pages (login, monitors, monitor detail, monitor form, incidents); removed the
  waterfall, charts and form libraries (`react-hook-form`, `zod`).
- Docs: 16 planning documents replaced by `ARCHITECTURE`, `DECISIONS` and `INTERVIEW_PREP`.

## [1.0.0] - 2026-10-15

### Added
- M0: project tooling (uv, ruff, mypy, pytest), dev `docker-compose.yml` with Postgres 16 + healthcheck, `.env.example`.
- M1: Alembic initial migration (`users`, `monitors`, `monitor_state`, `check_runs`), plain-GET probe via `python -m app.cli probe`, `GET /api/v1/monitors/{id}/runs`, minimal Faultline `/ok`, Postgres integration tests + CI job.
- M2: probe engine (`app/probe`: phase timings, failure taxonomy, assertions, retry, body cap, header allow-list), Faultline v1 + socket faults, migration 0002, CLI phase table, ADR-017.
- M3: SSRF guard (`app/probe/ssrf.py`) with IP-pinned connections, port/hostname rules, `SSRF_ALLOW_PRIVATE_CIDRS` dev override, rebinding and TLS-by-IP tests, ADR-018.
- M4: durable worker (`app/worker`): SKIP LOCKED claim with leases, fenced idempotent completion, bounded concurrency, heartbeat, graceful shutdown, DB-outage backoff; Dockerfile and compose services; ADR-019.
- M5: incidents (pure state machine, `incidents`/`incident_events`, one-open-incident unique index, evidence builder, diagnosis hints), applied transactionally in TX2; ADR-020.
- M6: auth (argon2id, hashed server-side sessions, CSRF header), monitor CRUD/pause/resume/check-now with SSRF pre-check and caps, runs (keyset pagination), stats, incidents, run detail, `/probe/test`, problem+json errors, IDOR route-crawler test; ADR-021.
- M7: React/Vite/TypeScript frontend (monitors, form with test request, monitor detail with chart and runs, run detail with phase waterfall, incidents), SPA served by the API with security headers, CI frontend job; ADR-022.
- M8: structured JSON logging with correlation ids, Prometheus metrics, `/healthz` `/readyz` `/metrics`, System page and `/api/v1/system/status`, batched retention job with `job_runs`, CLI `probe-url`/`retention`, DB-outage recovery test, runbook with real commands; ADR-023.
- M9: production compose (caddy TLS, hardened non-root read-only containers), Caddyfile, `.env.prod.example`, `ops/backup.sh` + `ops/restore-drill.sh`, `create-user` CLI, manual deploy workflow, CI ops job; ADR-024.
- M10: README with measured numbers and commands, `ops/load_experiment.py`, v1.0 security control review, CI `security` job (pip-audit, npm audit, gitleaks), Dependabot, coverage settings, `react-router-dom` 6.30.6 (fixes three high advisories).

Known gaps at this tag: not deployed to a VM/domain (M9 provisioning), no notifications, two moderate `react-router` advisories, in-process rate limiting. See README "Limitations".
