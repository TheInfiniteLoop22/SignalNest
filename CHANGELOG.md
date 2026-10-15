# Changelog

All notable changes are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- M0: project tooling (uv, ruff, mypy, pytest), dev `docker-compose.yml` with Postgres 16 + healthcheck, `.env.example`.
- M1: Alembic initial migration (`users`, `monitors`, `monitor_state`, `check_runs`), plain-GET probe via `python -m app.cli probe`, `GET /api/v1/monitors/{id}/runs`, minimal Faultline `/ok`, Postgres integration tests + CI job.
- M2: probe engine (`app/probe`: phase timings, failure taxonomy, assertions, retry, body cap, header allow-list), Faultline v1 + socket faults, migration 0002, CLI phase table, ADR-017.
- M3: SSRF guard (`app/probe/ssrf.py`) with IP-pinned connections, port/hostname rules, `SSRF_ALLOW_PRIVATE_CIDRS` dev override, rebinding and TLS-by-IP tests, ADR-018.
