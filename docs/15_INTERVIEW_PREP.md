# 15 — Interview Prep, Honest Resume Claims, Demo

## 1. Questions you MUST be able to answer

**Product & scope**
1. What does SignalNest do, for whom, and why not just use Uptime Kuma or Checkly? *(Answer honestly: you'd use them in production; this was built to learn and demonstrate reliability engineering.)*
2. What did you deliberately not build and why?

**Architecture**
3. Draw the architecture. Why two processes from one codebase?
4. Who owns which data? Why split `monitors` and `monitor_state`?
5. Walk through one scheduled probe end to end, including both transactions.

**Concurrency & scheduling**
6. How do you guarantee a monitor isn't probed twice for the same slot? (SKIP LOCKED + lease + unique constraint + fenced update — explain why each exists.)
7. What happens when a worker is killed mid-probe? When the DB goes down?
8. How do you bound concurrency? What happens when all slots hold hanging probes?
9. Fixed-rate vs fixed-delay; how do you avoid catch-up storms after downtime?
10. Why never hold a transaction across network I/O?

**Networking**
11. Explain DNS → TCP → TLS → HTTP for one request and what each timing measures.
12. Connection refused vs connect timeout vs reset vs read timeout — causes and packet-level differences.
13. Why disable connection reuse for probes? What's SNI?
14. Which failures do you retry, and why not the others?

**Data**
15. Schema walk-through; the partial unique index; why keyset pagination; indexes and how you verified them (`EXPLAIN ANALYZE`).
16. How is uptime % defined, and what are its limitations?

**Security**
17. What's SSRF; how does your guard work; what's DNS rebinding; what residual risk remains?
18. Sessions vs JWT — why you chose sessions here; CSRF defences.
19. How do you prevent IDOR?

**Reliability & ops**
20. How do you tell whether a failure is the target's or yours? (INCONCLUSIVE, heartbeats, canary.)
21. Runbook: "no new runs for 5 minutes" — what do you check?
22. Liveness vs readiness.

**Testing**
23. How do you test timeouts and TLS errors without the internet?
24. What does your CI prove?

## 2. Optional advanced questions
- How would you make this multi-region and decide an incident by quorum?
- At what scale would you replace Postgres-as-queue, and with what? What would you measure first?
- Exactly-once vs at-least-once: which does SignalNest provide for runs and for webhooks (outbox)?
- How would you partition `check_runs`? Time-series DB trade-offs?
- How would you add per-tenant fairness to the scheduler?
- Explain how an LLM summary could hallucinate here and how your design limits it (if you built ADR-014).

## 3. Fundamentals the code touches (revise these alongside)
TCP handshake/teardown · TLS 1.2/1.3 handshake · DNS resolution & TTL · HTTP/1.1 semantics, idempotency, status classes · Postgres MVCC, isolation levels, row locks · B-tree indexes · Python asyncio event loop, tasks, cancellation · process signals (SIGTERM vs SIGKILL) · hashing vs encryption.

## 4. Demo script (5 minutes)
1. *(30 s)* Problem statement + architecture diagram.
2. *(60 s)* Create monitors for Faultline `/health`, `/api/orders`, `/api/products` with a `json_equals` assertion. Use **Test request** first.
3. *(60 s)* `POST /scenario {"mode":"slow_db"}` → orders turns DEGRADED → open a run → phase waterfall shows TTFB spike while connect is normal.
4. *(60 s)* `{"mode":"outage"}` → 3 FAILs → incident opens → incident page: timeline, class counts, hint.
5. *(30 s)* `{"mode":"broken_contract"}` → ASSERTION_FAILED: "up but wrong".
6. *(30 s)* `{"mode":"healthy"}` → incident resolves after 2 successes.
7. *(30 s)* Try to add `http://169.254.169.254/` → blocked. Show System page; `kill -9` a worker live and show another picking up.

## 5. Discussing limitations honestly
- Single vantage point; sampled availability; no notifications in MVP (webhook in next iteration); not load-tested beyond what you measured; demo deployment is one VM with a container DB (not HA).
- Say: "In production I'd use an established tool; building this taught me X, Y, Z."

## 6. Resume bullet themes (fill numbers ONLY after measuring)
- Built **SignalNest**, a self-hosted API reliability workbench (FastAPI, PostgreSQL, React/TypeScript, Docker) that records per-phase timings (DNS/connect/TLS/TTFB) and classifies failures into ___ categories.
- Designed a restart-safe, horizontally scalable scheduler using PostgreSQL `SKIP LOCKED` leases and idempotent completion; verified exactly-once-per-slot execution with ___ concurrent workers under kill tests.
- Implemented SSRF defences (resolve-validate-pin, private/metadata range blocking, DNS-rebinding protection) covered by ___ table-driven tests.
- Built an incident state machine with failure/recovery thresholds and rule-based diagnosis, demonstrated with a custom fault-injection service.
- ___ automated tests in GitHub Actions (unit, Postgres integration, network-fault tests) with no external network dependency; deployed on ___ with HTTPS and tested backup restores.

Never write: "production-grade", "highly scalable", "99.9 % uptime", user counts, or any number you didn't measure.

## 7. Agile / collaboration — the honest framing
"Solo project, but run like a team project: issues per milestone on a board, small PRs gated by CI, ADRs for design decisions, a changelog, and regular self-reviews against a checklist." Don't claim sprints, standups or code review by others unless they happened.
