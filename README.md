# SignalNest — Planning Pack

> **SignalNest** is a small, self-hosted **API reliability workbench**: it probes HTTP endpoints on a schedule, records *phase-level evidence* for every check (DNS → connect → TLS → first byte), classifies *why* a check failed, groups failures into incidents, and ships with **Faultline**, a deliberately broken demo API that makes every failure mode reproducible on demand.

This folder is the complete planning pack. It is written to be dropped into the root of a new repository and used with Claude Code. **No implementation exists yet** — the plan must be reviewed and approved first.

---

## The one-paragraph pitch

Most uptime tools answer *"is it up?"*. SignalNest answers *"what exactly happened, and where did the time go?"* for a developer's own handful of endpoints. It deliberately does **not** compete with UptimeRobot, Better Stack or Checkly (multi-region probing, on-call, 90+ notification channels, status pages). Its value is the engineering underneath: a restart-safe Postgres-backed scheduler, bounded concurrency, a precise failure taxonomy, an incident state machine, SSRF-safe outbound requests, and deterministic tests against a fault-injection target — all small enough for one student to understand and defend end to end.

---

## How to read this pack

| # | File | Read it when… |
|---|------|---------------|
| 0 | [`CLAUDE.md`](CLAUDE.md) | Always loaded by Claude Code. The working agreement. |
| 1 | [`docs/00_EXECUTIVE_ASSESSMENT.md`](docs/00_EXECUTIVE_ASSESSMENT.md) | First. Is this worth building? Risks, decisions, name. |
| 2 | [`docs/01_COMPETITIVE_RESEARCH.md`](docs/01_COMPETITIVE_RESEARCH.md) | Before arguing about scope. Sourced comparison table. |
| 3 | [`docs/02_GAP_TO_LEARNING_MAP.md`](docs/02_GAP_TO_LEARNING_MAP.md) | To see which resume gaps the project closes — and which it doesn't. |
| 4 | [`docs/03_FEATURE_SPEC.md`](docs/03_FEATURE_SPEC.md) | The product: MVP / next / stretch, screens, acceptance criteria. |
| 5 | [`docs/04_ARCHITECTURE.md`](docs/04_ARCHITECTURE.md) | Components, data flow, failure behaviour, diagrams. |
| 6 | [`docs/05_DATA_MODEL.md`](docs/05_DATA_MODEL.md) | Tables, constraints, indexes, retention, transactions. |
| 7 | [`docs/06_API_SPEC.md`](docs/06_API_SPEC.md) | REST surface, errors, pagination, validation. |
| 8 | [`docs/07_PROBE_ENGINE_SPEC.md`](docs/07_PROBE_ENGINE_SPEC.md) | The heart: timeouts, timings, failure taxonomy, retries, assertions. |
| 9 | [`docs/08_ENGINEERING_DECISIONS.md`](docs/08_ENGINEERING_DECISIONS.md) | ADRs: every important choice, alternatives, interview questions. |
| 10 | [`docs/09_SECURITY.md`](docs/09_SECURITY.md) | Threat model, SSRF defence, abuse limits, residual risk. |
| 11 | [`docs/10_FAULTLINE_SPEC.md`](docs/10_FAULTLINE_SPEC.md) | The fault-injection demo/test target. |
| 12 | [`docs/11_TESTING_PLAN.md`](docs/11_TESTING_PLAN.md) | Test pyramid, deterministic network tests, CI gates. |
| 13 | [`docs/12_BUILD_PLAN.md`](docs/12_BUILD_PLAN.md) | **Phase-wise milestones**, study lists, checkpoints, first milestone. |
| 14 | [`docs/13_REPO_AND_DEVOPS.md`](docs/13_REPO_AND_DEVOPS.md) | Repo layout, env vars, Docker, CI, deployment. |
| 15 | [`docs/14_OBSERVABILITY_AND_RUNBOOK.md`](docs/14_OBSERVABILITY_AND_RUNBOOK.md) | Logs, metrics, health endpoints, "watching the watcher", runbook. |
| 16 | [`docs/15_INTERVIEW_PREP.md`](docs/15_INTERVIEW_PREP.md) | Questions, honest resume claims, demo script. |
| 17 | [`docs/adr/ADR_TEMPLATE.md`](docs/adr/ADR_TEMPLATE.md) | Template for any new decision made during the build. |

---

## Status

| Item | State |
|---|---|
| Research & critique | ✅ Done (see docs 00–02) |
| Direction & MVP | ⏳ **Awaiting your approval** |
| Implementation | ⛔ Not started — begins at Milestone 0 in `docs/12_BUILD_PLAN.md` |

## Decisions you need to make before Milestone 0

1. Approve (or change) the narrowed direction in `00_EXECUTIVE_ASSESSMENT.md` §4.
2. Approve the MVP boundary in `03_FEATURE_SPEC.md` §3.
3. Pick your cloud for Milestone 9 (AWS / Azure / GCP) — or skip cloud and keep Render. See ADR-012.
4. Decide whether the stretch **LLM incident brief** matters for your Optum application (ADR-014). It is *off the critical path* either way.
5. Answer the Milestone 0 readiness questions at the end of `12_BUILD_PLAN.md`.
