# 10 — Faultline (fault-injection demo target)

A tiny FastAPI app (+ one raw-socket helper) in `faultline/` that misbehaves on purpose. Used by: the demo, the test suite, and your own learning (Wireshark lab). **Not exposed publicly** in deployment — it runs only on the internal docker network (or not at all in prod).

## 1. Endpoints

| Path | Behaviour | Expected SignalNest classification |
|---|---|---|
| `GET /ok` | 200 `{"status":"ok"}` | PASS |
| `GET /slow?ms=1500` | sleep then 200 | PASS or DEGRADED (depending on `degraded_ms`) |
| `GET /hang` | accept, never respond | READ_TIMEOUT |
| `GET /status/{code}` | respond with that status | HTTP_UNEXPECTED_STATUS (or PASS if allowed) |
| `GET /json?status=down` | 200 `{"status":"down"}` | ASSERTION_FAILED with `json_equals status == ok` |
| `GET /flaky?fail_rate=0.3&seed=…` | deterministic pseudo-random 503s (seeded per request counter) | mix; good for incident threshold demos |
| `GET /big?kb=500` | large body | PASS with `body_truncated: true` |
| `GET /redirect?to=…` | 302 to given location | 3xx recorded, not followed |
| `GET /drip?bytes_per_s=10` | sends headers fast, body very slowly | TOTAL_TIMEOUT |
| `GET /scenario` + `POST /scenario` | current mode for `/api/*` (see §2) | — |
| `GET /api/products`, `/api/orders`, `/health` | "realistic" endpoints whose behaviour follows the active scenario | depends on scenario |

**Raw-socket helper** (`faultline/socket_faults.py`, asyncio TCP server on separate ports):
- port A: accepts then immediately sends RST (`SO_LINGER` 0) → CONNECTION_RESET
- port B: accepts then closes cleanly without a response → CONNECTION_RESET (RemoteProtocolError)
- a port with nothing listening → CONNECTION_REFUSED
- TLS listener with a self-signed / wrong-hostname cert (via `trustme`) → TLS_CERT_INVALID

## 2. Scenarios (for the demo)

`POST /scenario {"mode": "..."}` switches the behaviour of `/health` and `/api/*`:

| Mode | Behaviour |
|---|---|
| `healthy` | all 200, ~20 ms |
| `slow_db` | `/api/orders` TTFB 1.5–3 s; `/health` fine → demonstrates H_SERVER_SLOW |
| `outage` | `/api/*` return 503 |
| `broken_contract` | `/api/products` returns 200 with `{"items": null}` |
| `hang` | `/api/orders` hangs |

Optional `{"mode": "...", "duration_s": 300}` auto-reverts to `healthy` for hands-free demos.

## 3. Rules
- Deterministic by default (seeded randomness).
- No state except the current scenario (in memory).
- Excluded from coverage targets for the main app; has its own small tests.
- The SSRF allow-override for its docker-network CIDR exists **only** in dev/test compose files.
