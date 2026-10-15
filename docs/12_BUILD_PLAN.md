# 12 — Phase-wise Build Plan

```
CURRENT MILESTONE: M7 (not started)
```

Durations assume ~12–15 hrs/week alongside college and placements. They are **estimates for planning, not commitments**. If a milestone overruns by 50 %, stop and re-scope rather than rush.

## Overview

| Phase | Milestone | Deliverable | Est. |
|---|---|---|---|
| **A. Foundations** | M0 | Repo, tooling, CI skeleton, compose with Postgres | 2–3 days |
| | M1 | **Tiny vertical slice**: one hard-coded URL probed by a CLI, result stored, listed via API | 3–4 days |
| **B. The probe** | M2 | Probe engine: timings, taxonomy, assertions, Faultline v1 | 1–1.5 wks |
| | M3 | SSRF guard + IP pinning | 4–6 days |
| **C. The scheduler** | M4 | Worker: lease-based claiming, bounded concurrency, restart safety, graceful shutdown | 1.5 wks |
| **D. The product** | M5 | Incidents: state machine, events, evidence, hints | 1 wk |
| | M6 | Auth + monitor CRUD + runs/stats APIs | 1 wk |
| | M7 | Frontend MVP | 1.5 wks |
| **E. Operate it** | M8 | Observability, system page, retention, runbook | 4–6 days |
| | M9 | Deployment (cloud VM or Render), backups, HTTPS | 3–5 days |
| | M10 | Polish: README with measured numbers, demo script, threat model review, MVP tag `v1.0.0` | 3–4 days |
| **F. Next iteration** | M11+ | Webhook outbox, canary, rollups, redirects, e2e, (stretch) LLM brief | as time allows |

## Progress tracker

| Milestone | Status | Notes |
|---|---|---|
| M0 | ✅ Done | Scaffolding + CI green 2026-10-15; checkpoint quiz skipped (see deviations) |
| M1 | ✅ Done | 2026-10-15. 4 tests (1 unit, 3 integration) pass locally; CI has a Postgres service job. CLI run 3x -> API listed 3 runs newest-first |
| M2 | ✅ Done | 2026-10-15. 22 engine tests against real sockets + 37 unit tests; `mypy --strict app/probe` clean. ADR-017 written |
| M3 | ✅ Done | 2026-10-15. ~55-case IP table + 28 URL cases, rebinding/mixed-answer/pinning/TLS-by-IP tests; no `httpx` outside `app/probe` (enforced by a test). ADR-018 |
| M4 | ✅ Done | 2026-10-15. 16 worker integration tests (AC1-lite, AC2, AC3, AC11, AC12 + fencing); `docker compose up --scale worker=2` demo: 31 runs / 31 distinct slots, then SIGKILL of the worker mid-`/hang` probe -> lease expired and monitor rescheduled, 48 / 48. ADR-019 |
| M5 | ✅ Done | 2026-10-15. 219 tests total pass locally; state machine (23 cases incl. AC9), hints (14 cases), Faultline scenario tests for AC7/AC10 incl. backdated `opened_at` and event sequence. ADR-020 incl. the pause decision |
| M6 | ✅ Done | 2026-10-15. 268 tests total pass locally (49 new: 40 API integration + 9 unit). Auth, monitor CRUD/pause/resume/check, runs (keyset), stats, incidents, run detail, probe test, problem+json, IDOR crawler (AC8), SSRF create-time rejection (AC6). ADR-021 |
| M7–M10 | ⬜ Not started | |

### Deviations log
| Date | Milestone | Deviation | Reason |
|---|---|---|---|
| 2026-10-15 | M0 | Initial planning-pack commit pushed straight to `main`; M0 work is on branch `m0/foundations` | User asked for the docs commit on `main`; all later work follows branch-per-milestone |
| 2026-10-15 | M0 | Pair-built scaffolding started before the formal "study check" quiz | User explicitly said "proceed"; the quiz is deferred to the M0 checkpoint |
| 2026-10-15 | M0 | Tooling choice: **uv** (not pip-tools) | One tool for Python version, venv and lockfile (`uv.lock`); fast; needed to pin 3.12 |
| 2026-10-15 | M0 | `ruff` excludes `docs/` | ruff formats Python snippets inside Markdown, which would rewrite the spec's illustrative code |
| 2026-10-15 | M0 | `mypy --strict` not yet enabled on `app/probe`/`app/worker` | Those packages don't exist yet; strict overrides are added when they are created (M2/M4) |
| 2026-10-15 | M0 | GitHub Project board and branch protection deferred | Protection needs CI check names, which exist only after the CI workflow is written |
| 2026-10-15 | All | Workflow changed: **commit directly to `main`**, no branches/PRs/review gates; Claude acts autonomously | User request to minimise their involvement. Supersedes branch-per-milestone in `13` §6 and CLAUDE.md §1 hand-offs |
| 2026-10-15 | M0 | CI workflow written by Claude, not the learner | Same request; learner should still read it before the checkpoint quiz |
| 2026-10-15 | M0 | No branch protection or project board | Solo repo pushing straight to `main`; protection would block the chosen workflow |
| 2026-10-15 | M0 | `LEARNING_LOG.md` removed; readiness and checkpoint quizzes dropped | User: unnecessary file. Quizzes are optional on request |
| 2026-10-15 | M1 | Commands need `PYTHONPATH=backend` (e.g. `PYTHONPATH=backend uv run python -m app.cli seed`) | App is not an installed package (`tool.uv package = false`); revisit with the Dockerfile in M4/M9 |
| 2026-10-15 | M1 | `users` has only `id/email/created_at`; no `password_hash` yet | Auth is M6; that migration adds it. A seeded dev user stands in via `current_user` dependency |
| 2026-10-15 | M1 | Reduced `monitors`/`monitor_state`/`check_runs` columns (no headers, assertions, leases, timings, attempts) | Spec says "minimal columns" for M1; later migrations add them |
| 2026-10-15 | M1 | Failure class `UNCLASSIFIED_ERROR` for network exceptions | Placeholder until `classify.py` in M2 |
| 2026-10-15 | M1 | Integration tests override `get_session` with the test session | App's module-level async engine binds to one event loop; reusing it across pytest-asyncio loops fails on Windows |
| 2026-10-15 | M1 | Learner-owned pieces (model, migration, runs query) built by Claude | Autonomous workflow requested (see M0 deviations) |
| 2026-10-15 | M2 | `DNS_*` handled via an injected resolver and the engine resolves the host itself | httpcore exposes no DNS event (ADR-017); also required for IP pinning |
| 2026-10-15 | M2 | IP pinning (Host header + `sni_hostname`) implemented in M2, not M3 | DNS timing needs the engine to own resolution; M3 still owns the policy, the 40-case table and the TLS-by-IP spike/test |
| 2026-10-15 | M2 | `TargetPolicy` is a plain `Callable[[str], bool]` with `allow_all` default | Placeholder seam for M3's `ssrf.py`; `BLOCKED_BY_POLICY` path already tested with a fake policy |
| 2026-10-15 | M2 | Migration `0002` adds evidence columns; `probe_once` now calls the engine; Faultline gained the `/scenario` modes and `/flaky` early | Needed to persist what the engine measures; scenarios are cheap and used by M5 |
| 2026-10-15 | M2 | `UNCLASSIFIED_ERROR` removed; unknown exceptions -> `PROBE_INTERNAL_ERROR` (INCONCLUSIVE) | Per spec: never blame the target for our bug |
| 2026-10-15 | M2 | Learner-owned `classify.py` / `assertions.py` written by Claude | Autonomous workflow |
| 2026-10-15 | M2 | Not done: Wireshark lab, TLS-wrong-hostname test | Lab is manual learning; hostname-mismatch needs the M3 pinning spike (trustme CA injected) |
| 2026-10-15 | M3 | `TargetPolicy` callable replaced by `SsrfPolicy` object (validate_url + allows_ip); engine default is strict | A callable could not express URL/port/hostname rules; strict default means forgetting to configure fails closed |
| 2026-10-15 | M3 | Hostname rules return `BLOCKED_BY_POLICY` (INCONCLUSIVE), malformed URLs `INVALID_TARGET` (FAIL) | Policy denial is not evidence about the target (ADR-009); malformed config is |
| 2026-10-15 | M3 | Manual runs nudge `scheduled_for` past the latest slot | Unique slot key collided on coarse Windows clock; replaced by the real scheduler in M4 |
| 2026-10-15 | M3 | M1/M2 tests now pass an explicit loopback policy | The strict default correctly refuses 127.0.0.1 |
| 2026-10-15 | M4 | `monitors.interval_seconds` CHECK relaxed from 30..3600 to 1..3600 (migration 0003) | Lets worker tests use short intervals; the 30 s floor becomes API validation (`MIN_INTERVAL_S`, M6) |
| 2026-10-15 | M4 | Fixed migration constraint names: 0001 used names the metadata naming convention re-prefixed (`ck_monitors_ck_monitors_*`) | Found when 0003 tried to drop one; 0001 edited in place (nothing deployed yet) and a down/up cycle is verified |
| 2026-10-15 | M4 | AC1 verified as "one run per slot per interval" with a 30 s interval over ~3 s, not "10 +- 1 runs in 5 min" | A 5-minute test is too slow for CI; slot arithmetic is unit-tested in `test_schedule.py` |
| 2026-10-15 | M4 | Not done: automated DB-outage test, `docker compose` kill drill scripted in CI | Done manually only for kill -9; outage backoff is implemented but verified by reading, not by a test. Add in M8 |
| 2026-10-15 | M4 | Added shared SSL context in the engine | Per-probe CA-bundle loading blocked the event loop badly enough that 30 concurrent probes stalled for seconds |
| 2026-10-15 | M4 | `docker-compose.yml` now has migrate/api/worker/faultline, Dockerfile with 4 targets | Needed for `--scale worker=2`; frontend stage and prod compose come later |
| 2026-10-15 | M4 | Learner-owned claim SQL and `next_run_at` function written by Claude | Autonomous workflow |
| 2026-10-15 | M5 | Streak/incident counters stored in `monitor_state` (jsonb class-count maps) instead of recomputed from runs | Keeps `evaluate` pure and O(1); documented in ADR-020 |
| 2026-10-15 | M5 | `incidents` has no `class_counts` column; class counts for the API are derived from FAIL runs between the first failure and resolution | Single source of truth is `check_runs` for evidence, state for live counting |
| 2026-10-15 | M5 | Hint rules: all eight implemented plus `H_NO_BASELINE`; baseline needs >=20 PASS runs | Spec lists eight; an explicit "not enough history" hint is the honest fallback it describes |
| 2026-10-15 | M5 | Not done: `/flaky` threshold demo test; `slow_db` scenario hint test | `/flaky` exists in Faultline but is not asserted; `H_SERVER_SLOW` is unit-tested with synthetic baselines rather than a 20-run Faultline warm-up (would add ~minutes) |
| 2026-10-15 | M5 | Learner-owned `evaluate()` and two hint rules written by Claude | Autonomous workflow |
| 2026-10-15 | M6 | Rate-limit state is in-process (spec allowed in-DB or in-memory) | Simplest correct option; limitation stated in code and ADR-021 |
| 2026-10-15 | M6 | `/healthz`, `/readyz`, `/metrics`, `/system/status` not built here | They are M8 deliverables (F11/F13); `docs/06` lists them but the plan schedules them there |
| 2026-10-15 | M6 | Monitor list filters `?health=` in Python after the query | Max 50 monitors per user; avoids a second SQL path |
| 2026-10-15 | M6 | A name that does not resolve at create time is accepted | A target may not be deployed yet; execution re-validates |
| 2026-10-15 | M6 | OpenAPI "complete" is auto-generated; response examples not hand-written | Models are typed; examples can be added in M10 |
| 2026-10-15 | M6 | Learner-owned keyset cursor/runs query and stats SQL written by Claude | Autonomous workflow |
| 2026-10-15 | M6 | Seeded dev users from M1-M5 have an unusable `!` password hash until reseeded with the CLI | Auth needs a hash; reseed with `python -m app.cli seed --password ...` |

**MVP = M0–M10.** Order rationale: the riskiest technical unknowns (phase timing, IP pinning, SKIP LOCKED) come *before* auth and UI, so you discover problems while scope is still cheap to change. Auth arrives in M6; until then a single seeded dev user owns everything.

Each milestone below lists: **Deliverable · Study first · 🧑‍💻 You implement · Pair-built · Tests / Done when · Checkpoint questions.**

---

## M0 — Foundations
- **Deliverable:** repo structure (`13_REPO_AND_DEVOPS.md`), `pyproject.toml` (uv or pip-tools — pick one, note why), ruff/mypy configs, pytest with an "it runs" test, `docker-compose.yml` with Postgres 16 + healthcheck, `.env.example`, GitHub Actions running lint + tests, `CHANGELOG.md`, GitHub Project board with M1 issues.
- **Study first:** virtual envs & lockfiles; what a Docker healthcheck is; what CI runs on a PR vs on push.
- **🧑‍💻 You implement:** the CI workflow YAML (Claude reviews).
- **Done when:** a PR shows green CI; `docker compose up db` reaches healthy.
- **Status (2026-10-15):**
  - [x] repo layout (minimal: `backend/app`, `backend/tests/{unit,integration}`), `pyproject.toml` + `uv.lock`
  - [x] ruff / mypy / pytest configured; "it runs" test passes (`uv run pytest`)
  - [x] `docker-compose.yml` Postgres 16 + `pg_isready` healthcheck — verified `healthy`
  - [x] `.env.example`, `CHANGELOG.md`
  - [x] `.github/workflows/ci.yml` — lint / types / test jobs (written by Claude, see deviations)
  - [x] Project board / branch protection: dropped (see deviations)
- **Checkpoint:** Why a lockfile? What's the difference between `depends_on` and a healthcheck condition? What does CI prove and what doesn't it?

## M1 — Tiny vertical slice (end-to-end thinnest path)
- **Deliverable:** one Alembic migration with `monitors`, `monitor_state`, `check_runs` (minimal columns); `python -m app.cli probe <monitor_id>` runs a **plain** `httpx` GET (no timings yet), stores a run; FastAPI `GET /api/v1/monitors/{id}/runs` returns them. Seed script creates one dev user + one monitor pointing at a local `/ok` endpoint.
- **Study first:** SQLAlchemy 2.0 async session lifecycle; Alembic autogenerate vs hand-written; what `async def` route handlers do in FastAPI.
- **🧑‍💻 You implement:** the `check_runs` model + migration; the runs list query.
- **Pair-built:** app factory, settings (pydantic-settings), DB session dependency.
- **Done when:** run CLI 3×, `curl` lists 3 runs; integration test does the same against Postgres in CI.
- **Checkpoint:** Trace a request from `curl` to SQL and back. Why `expire_on_commit=False`? What happens if two CLIs run concurrently right now? (Nothing protects us yet — that's M4.)

## M2 — Probe engine
- **Deliverable:** `app/probe/engine.py`, `classify.py`, `assertions.py`; Faultline v1 (`/ok`, `/slow`, `/hang`, `/status`, `/json`, `/big`, `/drip`) + socket helper; CLI prints a phase table.
- **Study first:** TCP 3-way handshake, RST vs FIN; TLS handshake & SNI; HTTP/1.1 message framing; connect vs read timeout; `asyncio.timeout`. **Do the Wireshark lab** (`02` exercises).
- **🔬 Spike (first 1–2 days):** print httpcore trace events for an HTTPS request; confirm what you can measure. Write findings into ADR-017.
- **🧑‍💻 You implement:** `classify.py` (exception → class) against captured-exception fixtures; `assertions.py` incl. dotted JSON path.
- **Pair-built:** engine with trace hooks, body cap, retry logic.
- **Done when:** every class in `07` §4 except `BLOCKED_BY_POLICY`/`DNS_*` has an integration test that produces it for real; DNS classes via fake resolver; `mypy --strict app/probe` passes.
- **Checkpoint:** Explain each failure class and what the packets look like. Why not retry READ_TIMEOUT? Why disable keep-alive for probes? What does TTFB include?

## M3 — SSRF guard & IP pinning
- **Deliverable:** `app/probe/ssrf.py` with resolver injection; engine connects to the validated IP with Host/SNI = hostname; table-driven test suite (≥ 40 cases); `SSRF_ALLOW_PRIVATE_CIDRS` dev override.
- **Study first:** SSRF (OWASP), cloud metadata services & IMDSv2, DNS rebinding, IPv4-mapped IPv6, `ipaddress` module.
- **🔬 Spike:** verify TLS verification against hostname works when connecting by IP (trustme cert for `faultline.test`, fake resolver → 127.0.0.1 with override allowed).
- **🧑‍💻 You implement:** the IP classification function + its test table.
- **Done when:** AC6 passes; rebinding test passes; a code search shows no `httpx` usage outside `probe/`.
- **Checkpoint:** Five ways to say localhost. Why validate *all* resolved IPs? What's TOCTOU here and how does pinning fix it? What residual risk remains?

## M4 — Durable scheduler & worker ⭐ (core concurrency milestone)
- **Deliverable:** `app/worker/` — poll loop, claim (TX1), execute under `asyncio.Semaphore(MAX_CONCURRENCY)`, complete (TX2, idempotent + fenced), next_run_at rules (ADR-005), heartbeat row, graceful shutdown on SIGTERM/SIGINT, DB-outage backoff. `docker compose up --scale worker=2` works.
- **Study first:** row-level locks, `FOR UPDATE SKIP LOCKED`, READ COMMITTED; leases & fencing; asyncio tasks, `TaskGroup`, cancellation, signal handling; **do the isolation-level lab**.
- **🧑‍💻 You implement:** the claim SQL (`05` §4) and the `next_run_at` computation function (pure, tested).
- **Pair-built:** loop, semaphore, shutdown, heartbeat.
- **Done when:** AC1, AC2, AC3, AC11, AC12 pass in CI; manual test: kill -9 a worker mid-`/hang` probe and observe recovery in the DB; `EXPLAIN ANALYZE` of the claim query recorded in the ADR.
- **Checkpoint (expect a hard quiz):** Walk through two workers racing for the same row. What if TX2 commits after the lease expired and another worker already wrote the run? Why is no transaction open during the HTTP call? What's the max in-flight probes across 2 workers? What happens to in-flight probes on SIGTERM?

## M5 — Incidents
- **Deliverable:** `incidents/state_machine.py` (pure), applied inside TX2; `incidents`, `incident_events` tables; evidence builder (baseline medians); `hints.py`; Faultline scenarios + `/flaky`.
- **Study first:** finite state machines; hysteresis; partial unique indexes; percentiles vs medians.
- **🧑‍💻 You implement:** `evaluate()` and its table-driven tests; two hint rules.
- **Done when:** AC7, AC9, AC10 pass; scenario test `healthy → outage → healthy` yields exactly one incident with correct `opened_at` (backdated) and events.
- **Checkpoint:** Draw the state machine from memory. Why does INCONCLUSIVE not reset counters? What does the partial unique index protect against? Decide & justify: should pausing auto-resolve an open incident? (Write the ADR.)

## M6 — Auth, CRUD and read APIs
- **Deliverable:** users/sessions; register/login/logout/me; monitor CRUD with validation + SSRF pre-check + caps; pause/resume/check-now; runs (cursor pagination + filters); stats; incidents; `/probe/test` with rate limit; problem+json errors; IDOR route-crawler test.
- **Study first:** sessions vs JWT; CSRF & SameSite; argon2; keyset pagination; RFC 9457; rate limiting algorithms (fixed window vs token bucket — an in-DB or in-memory fixed window is enough; state the limitation with multiple API replicas).
- **🧑‍💻 You implement:** keyset cursor encode/decode + the runs query; the stats SQL (`percentile_cont`).
- **Done when:** AC8 passes; OpenAPI docs complete; every endpoint has a happy-path and an error-path test.
- **Checkpoint:** Why 404 not 403? Why keyset over offset? How is uptime % defined and what are its limits? What breaks if you run 2 API replicas with in-memory rate limits?

## M7 — Frontend MVP
- **Deliverable:** screens in `03` §7; TanStack Query with refetch on monitor detail (e.g. 10 s); phase waterfall component; forms with Zod; accessible status labels; served by the API container in prod.
- **Study first:** TanStack Query caching & invalidation; CSP implications for a Vite build; accessible colour use.
- **🧑‍💻 You implement:** the phase waterfall component (pure SVG/divs, no chart library) + its tests.
- **Done when:** full demo flow (`15` §4) works by clicking only; Vitest green; `tsc --noEmit` clean.
- **Checkpoint:** How does data get from Postgres to the waterfall? What happens in the UI when the API returns 401 mid-session? Why is the response snippet safe to display?

## M8 — Observability & operations
- **Deliverable:** JSON logs with `request_id`/`run_id`/`monitor_id`/`worker_id`; `/healthz`, `/readyz`; `/metrics` (`probe_duration_seconds` histogram by outcome, `runs_total{outcome,failure_class}`, `scheduler_lag_seconds`, `in_flight_probes`, `claim_batch_size`, `db_errors_total`); System page; retention job with `job_runs`; `14_OBSERVABILITY_AND_RUNBOOK.md` finalised with real commands.
- **Study first:** logs vs metrics vs traces; Prometheus metric types & label cardinality; liveness vs readiness.
- **🧑‍💻 You implement:** choose the metric labels and defend against cardinality explosion (why not `monitor_id` as a label?).
- **Done when:** you can answer runbook scenarios using only logs + metrics + System page; retention deletes in batches with a test.
- **Checkpoint:** Diagnose: "No new runs for 5 minutes." Walk through what you'd look at, in order.

## M9 — Deployment
- **Deliverable:** production compose profile (api, worker, db, caddy), non-root images, multi-stage Dockerfile, secrets via env file on host (not in repo), firewall rules, HTTPS, nightly `pg_dump` + one **tested restore**, deploy workflow (manual trigger is fine). Choose AWS/Azure/GCP VM or Render (ADR-012).
- **Study first:** DNS records (A/AAAA/CNAME), TLS certificates & ACME, security groups/firewalls, SSH hardening, backups vs replicas.
- **🧑‍💻 You implement:** the firewall/security-group rules and the restore drill (document every command).
- **Done when:** public HTTPS URL with registration disabled and a demo account; restore drill documented with timestamps.
- **Checkpoint:** What's exposed to the internet? How do secrets reach the box? How long would recovery take after losing the VM, and how do you know?

## M10 — Polish & v1.0.0
- **Deliverable:** README (problem, architecture diagram, how to run, demo GIF, **measured** numbers with the commands used — e.g. CI test count, a small load experiment: N monitors × interval on one worker, observed scheduler lag), limitations section, threat model re-review, `CHANGELOG`, tag `v1.0.0`.
- **Done when:** a friend can clone, `docker compose up`, and run the demo from the README without help.
- **Final checkpoint:** a 30-minute mock interview using `15_INTERVIEW_PREP.md` (Claude plays interviewer).

## M11+ — Next iteration (pick in order of interest)
1. Webhook outbox (`outbox_events` table, delivery worker loop, HMAC-SHA256 signature header, exponential backoff, dead-letter after N tries).
2. Probe canary → INCONCLUSIVE marking.
3. Hourly rollups + 90-day history.
4. Encrypted secret headers.
5. Safe redirect following.
6. Playwright e2e in CI.
7. (Stretch) LLM incident brief (ADR-014) — only after the grounding lab exercise.

---

## Regular checkpoints (process)
- **End of each milestone:** explain-back + quiz (CLAUDE.md §4).
- **Every 2 milestones:** "design from memory" — draw the architecture and the probe sequence on paper without looking; compare with docs; fix gaps.
- **Before M4 and M6:** 10-minute re-read of `08_ENGINEERING_DECISIONS.md` and challenge one ADR you now disagree with.

---

## ▶ First milestone in detail: M0 → M1

**Recommended first step:** M0 (half-week) then M1 immediately — together they form the tiny vertical slice.

### Definition of done (M0 + M1)
- [ ] Repo with the layout from `13`; README stub linking to docs.
- [ ] `docker compose up db` healthy; `alembic upgrade head` creates 3 tables.
- [ ] `python -m app.cli probe <id>` stores a run (status code + total_ms + outcome PASS/FAIL).
- [ ] `GET /api/v1/monitors/{id}/runs` returns runs newest-first.
- [ ] 1 unit test + 1 integration test (Postgres) pass locally **and** in GitHub Actions.
- [ ] ruff + mypy clean.

### Questions to answer before implementation begins
1. In your own words: what problem does SignalNest solve that a cron job running `curl` doesn't?
2. Why are `monitors` and `monitor_state` separate tables?
3. What is a database transaction, and why must we never keep one open while waiting on the network?
4. What does `async def` buy us in the worker that threads also could — and what's different?
5. Which three failure classes do you expect to be hardest to distinguish, and why?
6. What are you deliberately **not** building, and how will you explain that choice in an interview?
7. Which cloud (or Render) will you deploy to in M9, and why?

Readiness questions were waived (see deviations log).
