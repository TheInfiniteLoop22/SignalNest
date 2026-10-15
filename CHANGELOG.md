# Changelog

All notable changes are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
