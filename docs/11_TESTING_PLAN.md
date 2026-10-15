# 11 — Testing & Quality Plan

## 1. Principles
- **Deterministic:** no public internet, no real-time sleeps longer than ~1.5 s, seeded randomness.
- **Test behaviour at the seams** we own: pure functions in isolation, then DB + worker together against real Postgres.
- **Every failure class has at least one end-to-end test** that produces it for real (Faultline / socket helper / fake resolver).
- **Numbers are measured, not claimed:** coverage %, test counts and timings are recorded from CI output in the README only once they exist.

## 2. Layers

| Layer | Tooling | What | Examples |
|---|---|---|---|
| Unit (fast, no I/O) | pytest | `state_machine.evaluate`, `assertions`, `classify`, `ssrf` IP rules, `hints`, scheduling arithmetic, cursor encoding | Table-driven: thresholds 1/3/10; INCONCLUSIVE doesn't move counters; `::ffff:127.0.0.1` blocked; json path `items.0.id` on missing index |
| Property-based (optional) | hypothesis | state machine invariants; IP classifier | "Number of OPEN transitions ≤ number of FAIL streaks reaching N"; random IPs vs reference classification |
| Probe integration | pytest + Faultline in-process + socket helper + fake resolver + trustme | real HTTP/TCP/TLS behaviour on localhost | AC4, AC5, TLS cert mismatch, drip → TOTAL_TIMEOUT, retry then pass |
| DB integration | pytest + real Postgres (docker in CI service container; testcontainers optional locally) | migrations, constraints, claim query, TX2 idempotency | two concurrent claim calls never return the same monitor; duplicate `scheduled_for` insert is a no-op; partial unique index rejects a 2nd OPEN incident |
| Worker integration | pytest-asyncio, short intervals (configurable min interval in tests) | full loop | AC1–AC3, AC9, AC11, AC12 |
| API | httpx `AsyncClient` against the ASGI app | auth, validation, problem+json, IDOR, pagination, rate limits | Route-crawler test: for every route with `{id}`, user B gets 404 |
| Frontend unit | Vitest + Testing Library | form validation, phase waterfall rendering, outcome badges text labels | |
| E2E (next iteration) | Playwright | login → create monitor against Faultline → see FAIL run → incident opens | Runs in CI against docker compose |
| Migration | pytest | `alembic upgrade head` then `downgrade base` then `upgrade head` | |

## 3. Specific hard cases (must have tests)
1. Lease expiry while original worker still probing → only one run row, second state update skipped.
2. Worker `SIGTERM` during probe → leases released, exit 0 within grace.
3. Monitor deleted while being probed → TX2 handles FK absence gracefully (insert fails/no-ops, logged at INFO, no crash).
4. Monitor interval edited mid-flight → next_run_at uses the new interval (as defined in ADR-004).
5. Paused monitor never claimed; resume schedules promptly.
6. DB restarts during worker loop → loop backs off and recovers without process restart.
7. Rebinding: fake resolver returns public then private → second attempt `BLOCKED_BY_POLICY`.
8. Clock: scheduling computed with DB `now()` — test by freezing via SQL (`SET LOCAL` not possible for now(); instead inject a `now` parameter into the claim/complete functions and verify behaviour).

## 4. How to test timeouts deterministically
- Read timeout: Faultline `/hang` with `timeout_ms=1000` in tests.
- Connect timeout: prefer a seam — inject a transport/`connect` hook that awaits forever, so the timeout path is exercised without relying on OS networking quirks. Document the approach chosen in an ADR.
- DNS timeout: fake resolver that awaits beyond the budget.

## 5. Quality gates (CI)

```
lint        ruff check, ruff format --check, eslint, tsc --noEmit
types       mypy --strict app/probe app/worker app/incidents
test-unit   pytest -m "not integration"
test-int    pytest -m integration   (Postgres service container, Faultline in-process)
migrations  alembic upgrade/downgrade/upgrade
frontend    vitest run, vite build
security    pip-audit, npm audit --omit=dev --audit-level=high, gitleaks
docker      docker build (both targets); trivy image scan (next iteration)
```
Branch protection: `main` requires all gates green + PR. Coverage reported (`pytest --cov`); set a threshold only after measuring a baseline — don't pick a number up front.

## 6. Manual verification per milestone
Each milestone in `12_BUILD_PLAN.md` ends with a short manual script (run compose, flip Faultline scenario, observe UI/DB). Record results in the PR description.
