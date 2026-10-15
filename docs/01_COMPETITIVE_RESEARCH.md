# 01 — Competitive & Open-Source Research

Research done 15 Oct 2026 from official product pages and public repositories. **Verified** = stated on the linked official page/repo. **Inference** = my interpretation; treat as an opinion, not a fact. Feature lists and pricing change often — re-check anything decision-critical.

## 1. Products studied

| Product | Type | Source |
|---|---|---|
| Uptime Kuma | Open source, self-hosted | https://github.com/louislam/uptime-kuma |
| Gatus | Open source, self-hosted (+ managed offering) | https://github.com/TwiN/gatus |
| Healthchecks.io | Open source + hosted (cron / heartbeat) | https://healthchecks.io/about |
| UptimeRobot | Commercial SaaS | https://uptimerobot.com/website-monitoring/ |
| Better Stack Uptime | Commercial SaaS | https://betterstack.com/uptime |
| Checkly API Monitoring | Commercial SaaS | https://www.checklyhq.com/product/api-monitoring/ |

## 2. Comparison table

| | **Uptime Kuma** | **Gatus** | **Healthchecks.io** | **UptimeRobot** | **Better Stack** | **Checkly** |
|---|---|---|---|---|---|---|
| **Intended user** (verified unless noted) | Self-hosters wanting a self-hosted UptimeRobot alternative (stated motivation) | Developers; README pitches it as a developer-oriented health dashboard | Developers running cron jobs / scheduled tasks | Broad: devops, developers, marketers, support, business owners | Engineering teams; incident management + on-call | Developers / SRE / QA; "monitoring as code" |
| **Primary model** | Outside-in polling | Outside-in polling, YAML config | **Inside-out**: jobs ping the service ("dead man's switch") | Outside-in polling | Outside-in polling + incident platform | Outside-in API + browser checks, defined in TypeScript |
| **Check types** | HTTP(s), TCP, keyword, JSON query, WebSocket, ping, DNS, push, Docker, game servers | HTTP, ICMP, TCP, DNS, plus UDP/SSH/TLS/gRPC/WebSocket per FAQ | Heartbeat pings with period or cron schedule + grace time | Nine monitor types incl. keyword, ping, port, cron, DNS, API, UDP | Web, API, ping, SSL, domain, DNS, mail protocols, cron, Playwright | API (status, headers, body, latency assertions), multistep, browser |
| **Assertions / conditions** | Keyword, JSON query | Rich condition language over status, response time, body (JSONPath), IP, cert expiry | n/a | Keyword, custom headers | Not detailed on page | Status, headers (regex), JSONPath body, text body, response time |
| **Evidence on failure** | Ping chart, cert info | Condition results shown in UI | Ping log | Response time, SSL/domain info | Screenshots, error logs, traceroute/MTR for timeouts | Full request/response, **per-phase timing (DNS, TCP, first byte, download)**, OTel traces |
| **Slow vs down** | — | `[RESPONSE_TIME]` condition | — | Response-time monitoring | — | Separate *degraded* and *max* response-time thresholds |
| **Flakiness handling** | — (not stated in README) | Alert `failure-threshold` (default 3) / `success-threshold` (default 2) | Grace time | Recurring notifications, multi-location | Claims no false positives (marketing); incident merging | Retries: fixed, linear or exponential |
| **Min interval** | 20 s | 60 s default, configurable | Per check | 30 s | 30 s (3-min on free tier) | 10 s to 24 h |
| **Alerting** | 90+ notification services | Very many providers (Slack, PagerDuty, email, GitHub issues, …) | Email, SMS, integrations | Email, SMS, voice, Slack, webhook, … | SMS, voice, Slack, Teams, escalation policies, on-call | Slack, PagerDuty, webhooks; degraded vs failed routed separately |
| **Status pages** | Yes, multiple, custom domains | Yes (the dashboard itself) + announcements | Status page for its own service | Yes | Yes, branded subdomain | Yes |
| **Storage** | Not stated in README | memory, SQLite or Postgres | (Django app) | Proprietary | Proprietary | Proprietary |
| **Concurrency control** | Not stated | `concurrency` setting, default 3 | n/a | Proprietary | Proprietary | Proprietary |
| **Stack** | Node.js + Vue 3, WebSocket/Socket.IO SPA rather than REST | Go | Python + Django | — | — | TypeScript CLI, Playwright |
| **License** | MIT | Apache-2.0 | BSD (per secondary source) | — | — | — |

## 3. Notes per product

### Uptime Kuma
- **Verified:** self-hosted; monitor types listed above; 90+ notification services; 20-second intervals; multiple status pages mapped to domains; cert info; proxy and 2FA support; Docker install. The author's stated motivations include learning Vue 3 and trying WebSocket + SPA instead of a REST API.
- **Security history (verified, from published advisories):** stored XSS in the status-page analytics field (CVE-2023-49276); sessions not invalidated after a password change (CVE-2023-49804); local file read via `file://` URLs in the browser-screenshot monitor because the URL was not validated server-side (CVE-2024-56331); path traversal in plugin install.
- **Lesson:** a monitoring tool's core input — *a user-supplied URL* — is an attack surface. This directly justifies SignalNest's SSRF guard, scheme allow-list, and session revocation design.
- **Overkill for us:** 10+ check types, 90 notifiers, i18n, plugins.

### Gatus
- **Verified:** configured by YAML; conditions like `[STATUS] == 200`, `[BODY].status == UP`, `[RESPONSE_TIME] < 300`; alert thresholds `failure-threshold` default 3 and `success-threshold` default 2; storage memory/SQLite/Postgres; a global `concurrency` limit (default 3); "external endpoints" that receive pushed results and support a heartbeat interval; "suites" (alpha) for chained requests.
- **Verified motivation worth quoting in interviews (paraphrased):** metrics-based monitoring relies on real traffic, so if no traffic reaches your app (e.g. the load balancer is down), metrics stay quiet — synthetic checks catch that.
- **Lessons we adopt:** small, explicit condition vocabulary; consecutive-failure/success thresholds; bounded concurrency as a first-class setting.
- **Where we differ:** DB-backed config via a REST API + UI instead of a config file (gives us CRUD, auth and ownership to design).

### Healthchecks.io
- **Verified:** dead man's switch model — monitored jobs check in on a schedule; a missed check-in triggers an alert; supports cron expressions and grace time; open source and self-hostable; stated value "prefer simple and boring" technology.
- **Lesson:** the *opposite* direction of monitoring (inside-out). Useful as an interview contrast: when does push beat poll? (Private networks, batch jobs.) We do **not** build this in MVP; a push/heartbeat monitor is a stretch idea.

### UptimeRobot
- **Verified:** 30-second checks; nine monitor types; multi-location checks; maintenance windows; recurring notifications; custom HTTP headers; status pages; team roles (read / write / notify-only); SSL and domain-expiry alerts; free account advertised with 50 monitors.
- **Lesson:** this is the commodity baseline. Matching it feature-for-feature is pointless for a student project.

### Better Stack Uptime
- **Verified:** free tier advertised as 10 monitors, 10 heartbeats, a status page, 3-minute checks; fastest checks 30 s; screenshot + error log of failures; traceroute/MTR output to understand timeouts; incident merging; escalation policies; voice/SMS alerts; cron monitoring; Playwright transaction checks.
- **Lesson:** *evidence on failure* is valued commercially (screenshots, error messages, network traces). Our phase timings + failure class + response snippet are a student-scale version of the same idea.
- **Overkill:** on-call scheduling, escalations, voice calls.

### Checkly
- **Verified:** assertions on status code, headers, JSONPath body, text body and response time; two thresholds — *degraded* and *max* response time; retries (fixed/linear/exponential) to confirm a failure is real before alerting; per-phase timing for DNS, TCP, first byte and download; checks from 22 locations; 10 s–24 h intervals; checks defined as TypeScript and deployed from CI; OpenAPI import; an AI root-cause feature that reads run evidence.
- **Lessons we adopt (concepts, not code or UI):** "reachable is not the same as correct" → assertions; slow ≠ down → `DEGRADED` outcome; phase timings tell you *where the wait was*.
- **Interesting for the Optum angle:** Checkly's AI analysis reads stored evidence to name a likely cause. Our stretch LLM incident brief is the same *pattern* at toy scale, with strict grounding rules (ADR-014).

## 4. Category features — therefore NOT differentiators

Polling HTTP endpoints · status/latency charts · uptime % · status pages · notifications to Slack/email · SSL expiry · keyword checks · multi-location probing · maintenance windows · badges.

Any of these can exist in SignalNest, but none should be presented as "what makes it special".

## 5. What we intentionally will NOT build

| Not building | Why |
|---|---|
| Public status pages | Commodity; zero systems learning beyond templating. |
| Many notification channels | Commodity integration work. One signed webhook (next iteration) teaches the outbox pattern instead. |
| On-call / escalation / phone calls | Separate product category (incident management). |
| Multi-region probing | Needs multiple deployments; discuss as "what changes at scale". |
| Browser / Playwright checks | Heavy runtime, different problem. |
| ICMP / TCP / DNS / gRPC check types | Breadth over depth. HTTP alone exercises DNS, TCP, TLS and HTTP. |
| Monitoring-as-code CLI | Nice, but Checkly does it well; little extra learning vs. the REST API. |
| Prometheus/Grafana stack | We expose `/metrics`; we don't run a metrics platform (stretch at most). |
| Kubernetes, Redis, Celery, Kafka | No requirement justifies them at this scale (ADR-002/003). |

## 6. Answers to the brief's questions

**Is the direction still worth building?** Yes. The category is crowded, but the *engineering* inside it (scheduling, concurrency, failure classification, SSRF, incidents) maps almost one-to-one onto your listed gaps, and a reviewer immediately understands what the product does.

**A smaller, more distinctive, more educational angle?** *Failure forensics for your own few endpoints*, paired with a fault-injection target (Faultline) so every failure mode is reproducible. The distinctiveness is clarity and demonstrability, **not** a market gap — nobody needs another uptime SaaS.

**What makes it coherent without pretending to compete?** One honest sentence in the README: *"A learning-scale reliability workbench. For production monitoring use Uptime Kuma, Gatus, Checkly or similar."* Plus a demo that tells a story: outage → evidence → diagnosis → recovery.

## 7. Sources

- Uptime Kuma README — https://github.com/louislam/uptime-kuma
- Uptime Kuma advisories — https://github.com/louislam/uptime-kuma/security/advisories/GHSA-vr8x-74pm-6vj7 · https://advisories.gitlab.com/pkg/npm/uptime-kuma/CVE-2023-49276/ · https://advisories.gitlab.com/pkg/npm/uptime-kuma/CVE-2023-49804/ · https://releasealert.dev/cve/CVE-2024-56331
- Gatus README — https://github.com/TwiN/gatus (raw: https://raw.githubusercontent.com/TwiN/gatus/master/README.md)
- Healthchecks.io about — https://healthchecks.io/about
- UptimeRobot website monitoring — https://uptimerobot.com/website-monitoring/
- Better Stack Uptime — https://betterstack.com/uptime
- Checkly API Monitoring — https://www.checklyhq.com/product/api-monitoring/
- httptap (Python phase timing via httpcore trace hooks; feasibility evidence) — https://pypi.org/project/httptap/
