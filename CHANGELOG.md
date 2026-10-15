# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- Monitors with interval, timeout, expected status and body-contains checks.
- Worker that claims due monitors from Postgres (`FOR UPDATE SKIP LOCKED`, `locked_until` lease).
- Probe engine with DNS, connect, TLS and first-byte timings and 7 failure classes.
- SSRF guard: every resolved address is checked and the connection is pinned to the validated IP.
- Incidents: opened after N consecutive failures, resolved on the first pass.
- Accounts with server-side sessions; every query filters by `owner_id`.
- React UI (login, monitors, monitor detail, monitor form, incidents).
- Faultline, a breakable demo API used by the demo and the tests.
