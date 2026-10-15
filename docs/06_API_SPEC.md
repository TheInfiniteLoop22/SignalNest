# 06 — API Spec

Base path `/api/v1`. JSON only. FastAPI generates OpenAPI at `/api/docs` (disabled in production unless `EXPOSE_DOCS=true`).

## 1. Conventions

- **Auth:** httpOnly, `Secure`, `SameSite=Lax` cookie `sn_session`. State-changing requests must also send header `X-Requested-With: signalnest` (simple CSRF defence alongside SameSite; see `09_SECURITY.md` §5).
- **Ownership:** any id that isn't yours → `404 Not Found`.
- **Times:** ISO 8601 UTC (`2026-10-15T10:00:00Z`).
- **Errors:** RFC 9457 `application/problem+json`:
  ```json
  {"type":"https://signalnest.dev/problems/validation","title":"Validation failed",
   "status":422,"detail":"interval_seconds must be between 30 and 3600",
   "instance":"/api/v1/monitors","request_id":"01J…","errors":[{"field":"interval_seconds","message":"…"}]}
  ```
  | Status | When |
  |---|---|
  | 400 | malformed JSON |
  | 401 | no/expired session |
  | 404 | not found **or not yours** |
  | 409 | unique conflict (duplicate monitor name, email taken) |
  | 422 | validation failure, incl. `type=…/blocked-target` for SSRF-policy rejections |
  | 429 | rate limited (with `Retry-After`) |
  | 503 | DB unavailable |
- **Pagination:** keyset cursor. Response: `{"items":[…], "next_cursor":"<opaque base64 of (started_at,id)>" | null}`. `limit` default 50, max 200. Offset pagination is not offered (it degrades on large, constantly-growing tables and skips/duplicates rows as new runs arrive).
- **Request id:** every response has `X-Request-ID` (generated if absent); logged with every line.

## 2. Endpoints

### Auth
| Method | Path | Body / Params | Response |
|---|---|---|---|
| POST | `/auth/register` | `{email, password}` (12–128 chars) | 201 user; 403 if registration disabled |
| POST | `/auth/login` | `{email, password}` | 204 + cookie; 401 generic "invalid credentials"; 429 after 5 failures/15 min per email+IP |
| POST | `/auth/logout` | — | 204, session revoked |
| GET | `/auth/me` | — | `{id, email}` |

### Monitors
| Method | Path | Notes |
|---|---|---|
| GET | `/monitors` | List with embedded `state` (health, last_run_at, last failure_class) and 24 h summary (uptime, p95, sample_count). `?health=DOWN` filter. Max 50 per user, so no pagination. |
| POST | `/monitors` | Create. 422 on validation/SSRF; 409 on duplicate name; 422 `monitor-limit` when cap reached. |
| GET | `/monitors/{id}` | Config + state + open incident id. |
| PATCH | `/monitors/{id}` | Partial update; URL re-validated. |
| DELETE | `/monitors/{id}` | 204. |
| POST | `/monitors/{id}/pause` · `/resume` | 204; idempotent. |
| POST | `/monitors/{id}/check` | 202; sets `next_run_at = now()`; 409 if paused. Rate limit 1 per 10 s per monitor. |
| GET | `/monitors/{id}/runs` | `?cursor&limit&outcome=FAIL&failure_class=READ_TIMEOUT&from&to` |
| GET | `/monitors/{id}/stats` | `?window=24h|7d` → `{uptime_pct, sample_count, p50_ms, p95_ms, by_outcome:{}, by_failure_class:{}, series:[{bucket, p50, p95, fail_count}]}` |

### Runs
| Method | Path | Notes |
|---|---|---|
| GET | `/runs/{run_id}` | Full detail incl. attempts, assertion_results, redacted headers, snippet, `explanation` (plain-English text for the failure class). Ownership via monitor join. |

### Probe (unsaved test)
| Method | Path | Notes |
|---|---|---|
| POST | `/probe/test` | Body = monitor config subset (url, method, timeout_ms ≤ 10000, headers, assertions). Returns a run-shaped result, not stored. 6/min per user. |

### Incidents
| Method | Path | Notes |
|---|---|---|
| GET | `/incidents` | `?status=OPEN|RESOLVED&monitor_id&cursor&limit`; open first, then newest. |
| GET | `/incidents/{id}` | Incident + events + evidence `{first_failure_run, latest_failure_run, class_counts, baseline:{median_connect_ms, median_ttfb_ms, sample_count}, hints:[{code, text, evidence_run_ids}]}` |

### System & ops (outside `/api/v1`)
| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/healthz` | none | Liveness: process up. Never touches DB. |
| GET | `/readyz` | none | Readiness: `SELECT 1` within 1 s. |
| GET | `/metrics` | network-restricted or basic auth | Prometheus text format. |
| GET | `/api/v1/system/status` | session | Workers, scheduler lag, due count, in-flight, last retention job. |

## 3. Monitor schema (create)

```json
{
  "name": "Orders API",
  "url": "https://api.example.com/api/orders",
  "method": "GET",
  "interval_seconds": 60,
  "timeout_ms": 5000,
  "headers": {"Accept": "application/json"},
  "degraded_ms": 800,
  "failure_threshold": 3,
  "recovery_threshold": 2,
  "assertions": [
    {"type": "status_in", "values": [200]},
    {"type": "latency_below", "ms": 2000},
    {"type": "json_equals", "path": "status", "value": "ok"},
    {"type": "body_contains", "text": "orders"}
  ]
}
```

Validation rules (Pydantic + DB `CHECK`s both): URL scheme `http|https`, no userinfo (`user:pass@`), host present, port in allow-list (default 80, 443, 8000–8999), length ≤ 2048; headers ≤ 10, names RFC 7230 tokens, values ≤ 512 chars, denylist (`Host`, `Authorization`, `Cookie`, `Proxy-*`, `Connection`, `Content-Length`, `Transfer-Encoding`); ≤ 8 assertions; `degraded_ms < timeout_ms`.

## 4. Example run detail (abridged)

```json
{
  "id": 48213, "monitor_id": "…", "scheduled_for": "2026-10-15T10:00:00Z",
  "outcome": "FAIL", "failure_class": "READ_TIMEOUT",
  "explanation": "The TCP connection and TLS handshake succeeded, but the server did not send response headers within the 5000 ms timeout.",
  "timings": {"dns_ms": 2, "connect_ms": 11, "tls_ms": 24, "ttfb_ms": null, "total_ms": 5003},
  "resolved_ip": "203.0.113.10", "attempt_count": 1,
  "http_status": null, "assertion_results": [],
  "trigger": "schedule", "worker_id": "worker-7f3a"
}
```
