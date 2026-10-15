# 02 — Gap-to-Learning Map

Legend: **P** = address through this project · **E** = small separate exercise · **S** = interview study only · **V** = already evidenced; validate understanding instead of adding more.

| Gap (from brief) | Route | Where in SignalNest | By the end you can explain / demonstrate |
|---|---|---|---|
| Reliability & operations | **P** | Incidents, runbook, health endpoints, worker heartbeat | Incident lifecycle (detect → confirm → open → recover → resolve); why thresholds reduce false alarms; walk through the runbook for "checks stopped running". |
| Concurrency & background processing | **P** (core) | Worker, lease claiming, semaphore, graceful shutdown | Why `FOR UPDATE SKIP LOCKED` prevents double execution; what happens if a worker dies mid-probe; why we never hold a transaction across I/O; how `asyncio.Semaphore` bounds in-flight probes. |
| Networking in practice | **P** (core) | Probe engine, failure taxonomy, Faultline | The difference between connection refused, connect timeout, read timeout, connection reset and DNS NXDOMAIN — and how each looks in a packet capture; what TTFB measures; why keep-alive would distort timing. |
| System design | **P** + **S** | Architecture doc, "at scale" sections | Component boundaries, state ownership (config vs runtime state), the bottleneck at 10× and 1000× monitors, and what you'd change (partitioning, multi-region, queue). |
| Cloud platform | **P** (light, M9) | One VM on AWS/Azure/GCP with docker compose, TLS, firewall | What you provisioned, security groups/firewall rules, how secrets reach the box, how you'd move to managed Postgres. Do **not** claim more than one small deployment. |
| Operational observability | **P** | Structured logs, `/metrics`, `INCONCLUSIVE` outcome, scheduler lag | Separate target failures from monitor failures; correlate a run across logs by `run_id`; define the metrics you expose and their limits. |
| Security engineering | **P** (core) | SSRF guard, threat model, sessions, rate limits, IDOR rules | SSRF attack variants (private IPs, metadata IP, DNS rebinding, redirects, IPv6-mapped) and your defences; residual risk; why 404 not 403 for other users' resources. |
| Agile / team engineering | **E** | Run the project with GitHub Issues, milestones, PRs and a self-review checklist | Honest answer: "solo project, but I used issues per milestone, small PRs with CI gates, and ADRs for decisions." Don't overclaim team process. |
| LLM / API workflow | **E** (stretch inside project) | Optional LLM incident brief (ADR-014) | Grounding on structured evidence, refusal when evidence is thin, evaluation on labelled fixtures, not sending secrets to a third party. |
| Testing & CI | **V** + deepen | Deterministic network tests, Postgres integration tests in CI | How to test timeouts without sleeping for real; why tests must not hit the internet; what each CI job proves. |
| Full-stack, REST, SQL, auth, Docker | **V** | Used throughout, not the focus | Be ready to explain choices, but these are not what this project is "for". |
| DSA, OOP, DBMS, OS, CN theory | **S** | — | A project does not replace LeetCode-style practice or theory revision. Topics *touched* by the code (isolation levels, sockets, event loop) are flagged in `15_INTERVIEW_PREP.md`. |

## Suggested separate exercises (each ≤ 1 day)

1. **Wireshark lab:** capture a request to Faultline for each fault mode (`/reset`, `/hang`, refused port). Annotate SYN/SYN-ACK/RST/FIN. Save screenshots in `docs/learning/`.
2. **Isolation-level lab:** two `psql` sessions demonstrating `SKIP LOCKED` vs plain `FOR UPDATE` blocking.
3. **Agile hygiene:** create a GitHub Project board with one issue per milestone task before M1 starts.
4. **(Optional) LLM grounding lab:** 10 hand-written incident fixtures, prompt + evaluation script, before attempting the stretch feature.
