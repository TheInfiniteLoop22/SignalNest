# 00 — Executive Assessment

## 1. Verdict in one line

**Build it — but narrowed.** The "API reliability workbench" direction survives research, *provided* it is positioned as a small, deep, explainable system (failure forensics + a correct scheduler + safe outbound requests), not as a lighter UptimeRobot clone.

## 2. Problem, user, value

**User.** A developer (or a small team) who owns a handful of HTTP services — a side project, a college-fest backend, an internal tool — and wants to know when an endpoint misbehaves *and why*.

**Problem.** A red badge says *that* something failed. When a developer is debugging, the useful questions are different:

- Did DNS fail, did the TCP connection get refused, did TLS break, or did the server accept the connection and then go silent?
- Was the endpoint *wrong* (200 OK with a broken JSON body) rather than *down*?
- Is it slow because of the network or because the server is thinking (time-to-first-byte)?
- When did it start, how many checks failed, and when did it recover?
- Was the failure in the target — or in the monitor itself?

**Value SignalNest provides.** For each check: per-phase timings, a precise failure class, assertion results, a redacted response snippet. For each incident: a timeline of evidence and rule-based diagnosis hints ("connect time normal, first-byte time 9× its 7-day median → time is being spent inside the server"). Commercial tools offer much of this (Checkly shows phase timings; Better Stack records error evidence — see `01_COMPETITIVE_RESEARCH.md`). The point is not novelty; it is that **you build and can explain every layer**.

## 3. Fit with your portfolio

| Existing project | What it already proves | What SignalNest adds that it doesn't |
|---|---|---|
| GoPerch ERP | CRUD APIs, RBAC, ORM, Docker, deploy | Background processing, concurrency, failure handling |
| FindMine | Feature-rich full-stack, auth, realtime | Network fundamentals, reliability, threat modelling |
| FlowBench | SQL/ELT, analytics, dashboards | Long-running process, restart safety, ops |
| OnTime | ML evaluation, API, tests | Deterministic testing of network behaviour |

SignalNest is the **"systems & reliability"** project in the set. It deliberately reuses familiar tools (FastAPI, Postgres, React, Docker) so effort goes into *new concepts*, not a new stack.

## 4. The narrowed direction (what changed from the original brief)

| Original brief idea | Decision | Why |
|---|---|---|
| Generic uptime monitor with history | **Keep, but make "evidence" the core** | Charts and badges are commodity (every product in the study has them). Evidence + classification is where the learning is. |
| Many check types | **HTTP(S) only, GET/HEAD only in MVP** | Depth over breadth. GET/HEAD are safe and idempotent, which avoids side effects and simplifies retry reasoning. |
| Alerts | **In-app incidents only in MVP; one signed webhook in next iteration** | Notification channels are the most commodity feature in the category (Uptime Kuma lists 90+). The outbox-pattern webhook teaches more than 10 integrations. |
| Status page | **Excluded** | Commodity; little interview value. |
| (new) **Faultline** demo target | **Added** | Makes demos and tests deterministic; teaches *how* each failure looks at the socket level. |
| (new) **SSRF-safe probing** | **Added as a first-class requirement** | Any tool that fetches user-supplied URLs is an SSRF vector; this product class has real CVEs around URL handling. |
| (new) **"Watching the watcher"** | **Added** (worker heartbeat, scheduler lag, inconclusive outcomes) | Directly addresses the "separate application failures from target failures" gap. |
| LLM features | **Stretch only**, grounded on stored evidence | Useful for the Optum JD, but must not distort the core. |

## 5. Biggest risks & unknowns

| Risk | Likelihood | Mitigation |
|---|---|---|
| Scope creep into "mini-Datadog" | High | Hard MVP boundary (`03_FEATURE_SPEC.md` §3). "Not building" list. |
| Async Python + SQLAlchemy async is new territory | Medium | Milestone 1 is a tiny vertical slice; M4 has a dedicated asyncio study list. |
| Phase timing via httpx/httpcore trace hooks behaves unexpectedly | Medium | Spike in M2 before committing; fallback = total time + TTFB only. An existing tool (httptap) builds phase timing on these hooks, so it is feasible. |
| IP-pinned requests with correct TLS SNI/hostname verification | Medium | Spike in M3; documented fallback (validated resolver + re-check after connect). |
| Project looks like "yet another uptime monitor" on a resume | Medium | Resume bullets lead with the engineering (lease-based scheduler, SSRF guard, failure taxonomy), not "built a monitoring app". |
| Using Claude Code too heavily and not owning the code | Medium | `CLAUDE.md` learner-owned pieces + checkpoint quizzes. |
| Free-tier cloud limitations / cost | Low–Med | Cloud is M9 and optional; single VM + docker compose. Verify current pricing yourself before deploying. |

## 6. Key design decisions (detail in `08_ENGINEERING_DECISIONS.md`)

1. **Modular monolith, two processes** from one codebase: `api` and `worker`.
2. **Postgres is the job queue** — lease-based claiming with `SELECT … FOR UPDATE SKIP LOCKED`. No Redis/Celery.
3. **Config vs runtime state split**: `monitors` (owned by users) vs `monitor_state` (owned by the worker).
4. **Probe = two short transactions around network I/O**, never one long one.
5. **Run outcomes include `INCONCLUSIVE`**, so monitor-side faults never count against the target.
6. **Incidents via a pure state-machine function** with consecutive-failure / consecutive-success thresholds and a partial unique index guaranteeing one open incident per monitor.
7. **Opaque, hashed session tokens** in an httpOnly cookie (deliberately different from your JWT experience at GoPerch — it teaches revocation).
8. **React + Vite SPA**, not Next.js — no SSR need, and it avoids repeating FindMine's stack.

## 7. Name check

"SignalNest" — a web search found no monitoring/observability product by that name. Mild association risks: "Nest" also evokes Google Nest and the NestJS framework. **Recommendation: keep it.** It is distinctive enough and the product matters more than the name. Re-check GitHub/npm/PyPI name availability before publishing a package.

## 8. What "done" looks like

A public repo where a reviewer can run `docker compose up`, open the UI, flip Faultline into "outage" mode, and watch: checks fail with the correct class → an incident opens after 3 failures → the incident page shows phase-timing evidence and a diagnosis hint → Faultline recovers → the incident resolves after 2 successes. Plus: a CI badge, a threat model, a runbook, measured numbers in the README, and a 5-minute demo you can give from memory.
