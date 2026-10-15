# 03 — Feature Spec

## 1. Product statement

SignalNest lets a developer register HTTP endpoints, probes them on a schedule, records evidence for every check, classifies failures precisely, and groups consecutive failures into incidents with a timeline and diagnosis hints.

## 2. Core concepts (glossary)

| Term | Meaning |
|---|---|
| **Monitor** | A user-owned configuration: URL, method (GET/HEAD), interval, timeout, assertions, thresholds. |
| **Check run** (run) | One scheduled execution of a monitor. May contain up to 2 attempts (one retry). |
| **Attempt** | One HTTP request. Has phase timings and an error, if any. |
| **Outcome** | Per-run verdict: `PASS`, `DEGRADED`, `FAIL`, `INCONCLUSIVE`. |
| **Failure class** | *Why* a run failed: `DNS_FAILURE`, `CONNECTION_REFUSED`, `CONNECT_TIMEOUT`, `TLS_ERROR`, `READ_TIMEOUT`, `HTTP_UNEXPECTED_STATUS`, `ASSERTION_FAILED`, … (full list: `07_PROBE_ENGINE_SPEC.md` §4). |
| **Health state** | Per-monitor: `UNKNOWN`, `UP`, `DEGRADED`, `DOWN`, `PAUSED`. |
| **Incident** | An `OPEN`→`RESOLVED` episode started by N consecutive `FAIL`s and closed by M consecutive non-failures. |
| **Diagnosis hint** | A deterministic, rule-based sentence explaining what the evidence suggests. Never ML/LLM in MVP. |
| **Faultline** | The bundled fault-injection target used for demos and tests. |

## 3. MVP (must ship)

Complexity: **S** ≤ 1 day · **M** 2–4 days · **L** 1–2 weeks (part-time student pace; estimates, not promises).

| # | Feature | User value | Engineering / learning value | Depends on | Size |
|---|---|---|---|---|---|
| F1 | **Monitor CRUD** (name, URL, GET/HEAD, interval 30 s–1 h, timeout 1–30 s, expected status, max-latency & degraded-latency thresholds, failure/recovery thresholds, pause/resume) | Configure what to watch | Validation, ownership, migrations | Auth (F9) for ownership | M |
| F2 | **Probe engine** with phase timings (DNS, connect, TLS, TTFB, total) and failure taxonomy | Know *why* it failed and where time went | Sockets/TLS/HTTP semantics, async timeouts, exception mapping | — | L |
| F3 | **Assertions**: status in set/range, latency < X, body contains text, JSON field equals value (simple dotted path) | "Reachable ≠ correct" | Small evaluator design, pure functions, tests | F2 | M |
| F4 | **Durable scheduler + worker** (lease-based, bounded concurrency, restart-safe, no backfill storms) | Checks keep running reliably | The core concurrency milestone | F1, F2 | L |
| F5 | **Run history** with filtering (outcome, failure class, time range) and cursor pagination | See what happened | Keyset pagination, indexes | F4 | M |
| F6 | **Stats per monitor**: uptime %, p50/p95 latency, failure-class breakdown for 24h / 7d | Trends at a glance | SQL aggregates, percentile functions, metric definitions | F5 | S |
| F7 | **Incidents**: open/resolve via state machine, incident timeline, evidence (first failing run, class counts, timings vs baseline), diagnosis hints | Story of an outage | State machines, transactional invariants, partial unique index | F4 | L |
| F8 | **Outbound safety**: SSRF guard, scheme/port allow-list, IP pinning, no redirects followed, response size cap, per-user monitor cap | Tool can't be turned against its own network | Security engineering | F2 | M |
| F9 | **Auth**: register (can be disabled), login, logout, session cookie; owner-scoped access | Private data | Sessions vs JWT, password hashing, CSRF, IDOR | — | M |
| F10 | **"Test request" button** (run a probe once without saving) | Fast feedback while configuring | Rate limiting a synchronous expensive endpoint | F2, F8 | S |
| F11 | **System status**: worker heartbeats, scheduler lag, in-flight probes, last error | Is the *monitor itself* healthy? | Watching the watcher | F4 | S |
| F12 | **Faultline** demo target with scripted scenarios | Reproducible demo & tests | Failure modes at the socket level | — | M |
| F13 | **Ops basics**: `/healthz`, `/readyz`, JSON logs with correlation IDs, `/metrics`, retention job | Operable | Observability fundamentals | F4 | M |
| F14 | **UI**: login, monitors list, monitor detail, run detail (phase waterfall), incidents list/detail, system page, create/edit form | Usable product | SPA data fetching, charts, forms | API | L |

## 4. Next iteration (justified by the core workflow)

| Feature | Why it follows naturally | Size |
|---|---|---|
| **Signed webhook notifications via an outbox table** (incident opened/resolved) | Incidents exist but nobody is told. Outbox pattern = at-least-once delivery without a broker; HMAC signature = integrity. | M |
| **Probe canary** — worker periodically probes a control target; if the canary fails, concurrent runs are marked `INCONCLUSIVE` | Strengthens the "monitor vs target" separation | S |
| **Hourly rollup table** for stats beyond 7 days; raw runs kept 14 days | Retention vs. history trade-off | M |
| **Encrypted secret headers** (e.g. `Authorization`) with a key from env | Real APIs need auth; teaches envelope/at-rest encryption basics | M |
| **Optional redirect following** (≤ 3 hops, each hop re-validated by the SSRF guard) | Common real need; dangerous if done naively | S |
| **Playwright e2e smoke test** in CI | Confidence in the UI path | S |

## 5. Stretch (must not delay MVP)

- **LLM incident brief** (ADR-014): summarises an incident *only* from stored evidence JSON, cites run IDs, says "insufficient evidence" when appropriate; off by default; evaluated on labelled fixtures.
- **OpenAPI import**: pick GET paths from a spec to create monitors.
- **Push/heartbeat monitors** (Healthchecks-style inside-out monitoring).
- **Prometheus + Grafana** compose profile scraping `/metrics`.
- **Second probe location** (a second worker in another region tagging runs with `location`).

## 6. Deliberately excluded

Status pages · multiple notification channels · on-call/escalation · non-HTTP check types · POST/PUT/DELETE probes · browser checks · multi-tenant orgs/teams/RBAC · billing · mobile app · Kubernetes/Redis/Celery. See `01_COMPETITIVE_RESEARCH.md` §5 for reasons.

## 7. Screens (MVP)

1. **Login / Register** — email + password. Register can be disabled with `ALLOW_REGISTRATION=false`.
2. **Monitors list** — table: name, health badge, last check (relative time), last failure class, 24h uptime %, 24h p95, sparkline of last 50 runs coloured by outcome. Filters: health state. Empty state links to "Create monitor" and to the Faultline demo instructions.
3. **Create / Edit monitor** — form (React Hook Form + Zod mirrors backend validation) with a **Test request** button that shows a single result inline before saving.
4. **Monitor detail** — header (health, open incident banner), latency chart (p50/p95 per bucket) with a phase breakdown toggle, outcome strip, failure-class breakdown, runs table (filterable, paginated), pause/resume, "Check now".
5. **Run detail** — outcome, failure class with plain-English explanation, **phase waterfall** (DNS → connect → TLS → TTFB → body), attempts (if retried), assertion results (pass/fail with expected vs actual), resolved IP, response status/headers (redacted) and body snippet rendered as text.
6. **Incidents** — list (open first), detail with timeline events, evidence panel, diagnosis hints, links to runs.
7. **System** — workers (id, started, last seen, in-flight), scheduler lag, due-monitor count, retention job last run.

UX rules: every colour-coded state also has a text label; all times shown in local time with UTC on hover; never render response bodies as HTML.

## 8. Acceptance criteria (MVP-level, testable)

| ID | Given / When / Then |
|---|---|
| AC1 | Given a monitor with interval 30 s and one worker running, when 5 minutes pass, then 10 ± 1 runs exist and no two share `(monitor_id, scheduled_for)`. |
| AC2 | Given two workers, when both poll at the same time, then each due monitor is executed exactly once per scheduled slot. |
| AC3 | Given a worker killed with `SIGKILL` mid-probe, when the lease expires, then another worker (or the restarted one) runs the monitor; no run is duplicated. |
| AC4 | Given Faultline `/hang` and timeout 2 s, then the run is `FAIL / READ_TIMEOUT` and `total_ms` is between 2000 and ~2300. |
| AC5 | Given a refused port, then `FAIL / CONNECTION_REFUSED` with `connect_ms` set and no TLS/TTFB timings. |
| AC6 | Given a URL resolving to `127.0.0.1`, `10.x`, `169.254.169.254`, `::1` or `::ffff:127.0.0.1`, then creation **or** execution yields `BLOCKED_BY_POLICY` / HTTP 422 and **no packet is sent** to that address. |
| AC7 | Given failure threshold 3, when 3 consecutive `FAIL`s occur, then exactly one `OPEN` incident exists; after 2 consecutive non-`FAIL` runs it becomes `RESOLVED`. |
| AC8 | Given user A's monitor, when user B requests it by id, then 404. |
| AC9 | Given the worker's own DNS resolver is broken (injected), then runs are `INCONCLUSIVE`, do not count toward incidents, and are excluded from uptime %. |
| AC10 | Given a 200 response with JSON `{"status":"down"}` and assertion `status == "up"`, then `FAIL / ASSERTION_FAILED` with expected vs actual recorded. |
| AC11 | Given a worker stopped for 10 minutes, when it restarts, then each monitor runs once promptly (no backlog of 20 catch-up runs). |
| AC12 | Given `SIGTERM`, the worker stops claiming, finishes or abandons in-flight probes within the grace period, releases leases, and exits 0. |
