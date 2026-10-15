# 07 — Probe Engine Spec

The probe engine turns a monitor config into a `ProbeResult`. It does **no database I/O**. It is the most interview-relevant module: every line maps to a networking concept.

## 1. Interface

```python
@dataclass(frozen=True)
class ProbeRequest:
    url: str
    method: Literal["GET", "HEAD"]
    timeout_ms: int
    headers: Mapping[str, str]
    assertions: Sequence[Assertion]
    degraded_ms: int | None

@dataclass(frozen=True)
class AttemptResult:
    started_at: datetime
    resolved_ip: str | None
    dns_ms: int | None; connect_ms: int | None; tls_ms: int | None
    ttfb_ms: int | None; total_ms: int
    http_status: int | None
    error_class: FailureClass | None
    error_detail: str | None

@dataclass(frozen=True)
class ProbeResult:
    outcome: Outcome                 # PASS | DEGRADED | FAIL | INCONCLUSIVE
    failure_class: FailureClass | None
    attempts: list[AttemptResult]    # 1 or 2
    assertion_results: list[AssertionResult]
    response_headers: dict[str, str] | None   # redacted
    response_snippet: str | None

async def run_probe(req: ProbeRequest, *, resolver: Resolver, policy: TargetPolicy,
                    clock: Clock = monotonic_clock) -> ProbeResult: ...
```

`resolver`, `policy` and `clock` are **injected** so tests are deterministic (fake DNS answers, fake time where needed).

## 2. Execution steps (one attempt)

1. **Parse & validate URL** (scheme, no userinfo, port allow-list). Failure → `INVALID_TARGET` (`FAIL`; config is wrong, not the monitor).
2. **Resolve** host with `resolver.resolve(host, timeout)` → list of IPs. Time it → `dns_ms`.
   - NXDOMAIN → `DNS_FAILURE`. Temporary failure (EAI_AGAIN/timeout) → `DNS_TIMEOUT`.
   - If the host is a literal IP, `dns_ms = None`.
3. **Policy check** every resolved IP (`09_SECURITY.md` §3). If *any* is blocked → `BLOCKED_BY_POLICY` (`INCONCLUSIVE` for scheduled runs; 422 for create/test). Rejecting when *any* is blocked (not just the chosen one) avoids games with mixed answers.
4. **Pick one IP** (first IPv4, else first IPv6). Build the request to `scheme://<ip>:<port>/path?query`, with `Host: <hostname>` header and the `sni_hostname` request extension so TLS SNI and certificate verification use the real hostname. **🔬 Spike in M3**: confirm with a test that a cert for `localhost` validates and a mismatched name fails.
5. **Send** with a fresh connection (no keep-alive, no pooling) so connect/TLS are measured every time and no connection to a previous IP is reused. `follow_redirects=False`.
6. **Timings** from httpx/httpcore **trace hooks** (`extensions={"trace": cb}`): record monotonic timestamps at `connect_tcp.started/complete`, `start_tls.started/complete`, `receive_response_headers.started/complete`; derive `connect_ms`, `tls_ms`, `ttfb_ms` (headers-started → headers-complete is the server "think time" + first byte). **🔬 Spike in M2**: print the exact event names your installed httpcore emits; fallback if unavailable = total + TTFB only.
7. **Read body** streaming, stop at 64 KB (enough for assertions); keep first 2 KB as snippet. Larger bodies are not an error — `body_truncated: true`; assertions only see the first 64 KB.
8. **Deadline**: whole attempt wrapped in `asyncio.timeout(timeout_ms)`; per-phase httpx `Timeout(connect=…, read=…, write=…, pool=…)` set to the same budget so the *specific* phase that stalled raises first and gives a precise class.

## 3. Outcome rules

```
if probe-side error (worker bug, resolver unusable, BLOCKED in schedule)  → INCONCLUSIVE
elif network/protocol/TLS error                                           → FAIL
elif status not allowed or any assertion failed                           → FAIL
elif degraded_ms and total_ms > degraded_ms                               → DEGRADED
else                                                                      → PASS
```
Default "allowed status" when no `status_in` assertion: 200–399 (3xx counts as reachable because redirects aren't followed; ADR-010).

## 4. Failure taxonomy

| Class | Raised when | Typical cause | Outcome |
|---|---|---|---|
| `INVALID_TARGET` | URL fails validation at run time (e.g. config edited via DB) | config error | FAIL |
| `DNS_FAILURE` | NXDOMAIN / no address | typo, expired domain, missing record | FAIL |
| `DNS_TIMEOUT` | resolver timeout / EAI_AGAIN | resolver or upstream DNS trouble | FAIL (retryable) |
| `BLOCKED_BY_POLICY` | resolved IP in a denied range | SSRF attempt or DNS change to private IP | INCONCLUSIVE |
| `CONNECTION_REFUSED` | TCP RST in reply to SYN | nothing listening on that port | FAIL |
| `CONNECT_TIMEOUT` | no SYN-ACK within budget | firewall dropping packets, host down | FAIL (retryable) |
| `CONNECTION_RESET` | RST/abrupt close after connect, or `RemoteProtocolError` (server closed without response) | crashing process, proxy issues | FAIL (retryable) |
| `TLS_CERT_INVALID` | certificate verification failure | expired/self-signed/hostname mismatch | FAIL |
| `TLS_ERROR` | other handshake failure | protocol/cipher mismatch | FAIL |
| `READ_TIMEOUT` | connected, but no response headers in time | server overloaded/stuck | FAIL |
| `TOTAL_TIMEOUT` | overall deadline hit while reading body | slow body transfer | FAIL |
| `HTTP_UNEXPECTED_STATUS` | status not allowed (`detail` carries 4xx vs 5xx) | app error / wrong route | FAIL |
| `ASSERTION_FAILED` | any assertion false | wrong content, broken contract | FAIL |
| `PROBE_INTERNAL_ERROR` | unexpected exception in our code | **our** bug | INCONCLUSIVE |

🧑‍💻 `classify.py` (exception → class) is a learner-owned piece in M2. Tests give you real exceptions captured from Faultline; you write the mapping. Unknown exceptions must map to `PROBE_INTERNAL_ERROR`, never silently to a target failure.

## 5. Retry policy

- **At most one retry** within a run, after 1 s, **only** for retryable classes: `DNS_TIMEOUT`, `CONNECT_TIMEOUT`, `CONNECTION_RESET`.
- Never retry: HTTP status/assertion failures (deterministic), `READ_TIMEOUT` (already waited the full budget; retrying doubles load on a struggling server), `CONNECTION_REFUSED` (deterministic), TLS errors.
- The retry must fit in the run's lease; lease = `2 × timeout + 15 s`.
- Both attempts are stored (`attempts` jsonb); the run's columns reflect the **final** attempt. A run that passed on retry is `PASS` but the UI shows "passed after retry" — evidence is never hidden.
- GET/HEAD are idempotent (RFC 9110), so retrying them has no side effects — one reason POST is excluded.

Thresholds (3 failures to open) handle flakiness at the incident level; the single retry handles flakiness at the run level. Explain both layers in interviews.

## 6. Assertions (MVP set)

| type | params | Evaluates |
|---|---|---|
| `status_in` | `values: int[]` or `range: [lo, hi]` | `http_status` |
| `latency_below` | `ms` | `total_ms` of final attempt |
| `body_contains` | `text` (≤ 200 chars) | substring in first 64 KB, case-sensitive |
| `json_equals` | `path` (dotted, `items.0.id`), `value` (scalar) | parse body as JSON; walk path; compare with type-aware equality |

Each yields `{type, expected, actual, passed, message}`. If JSON can't be parsed, `json_equals` fails with `actual: "<not JSON>"`. 🧑‍💻 The dotted-path walker and evaluator are learner-owned (M2) — no JSONPath library; ~40 lines and a good recursion/edge-case exercise.

## 7. Response handling & redaction

- Store response headers only from an allow-list: `content-type`, `content-length`, `server`, `date`, `cache-control`, `retry-after`, `location`, `x-request-id`, `via`. Never `set-cookie`.
- Snippet: first 2 KB, UTF-8 with replacement chars, stored as text; UI renders with `textContent`, never `innerHTML`.
- `error_detail`: exception message truncated to 500 chars with the resolved IP and our own paths stripped.

## 8. Diagnosis hints (deterministic rules, in `incidents/hints.py`)

Baseline = medians over the last 7 days of `PASS` runs (needs ≥ 20 samples, else "not enough history").

| Code | Rule | Text template |
|---|---|---|
| `H_DNS` | dominant class DNS_* | "Name resolution is failing — check the domain's DNS records or registration." |
| `H_REFUSED` | dominant CONNECTION_REFUSED | "The host is reachable but nothing accepts connections on port {port}. The service may be stopped or listening on another port." |
| `H_NET_DROP` | dominant CONNECT_TIMEOUT | "Connection attempts get no reply — typical of a firewall dropping packets or a host that is down." |
| `H_SERVER_SLOW` | READ_TIMEOUT or DEGRADED, connect_ms ≤ 2× baseline, ttfb_ms ≥ 3× baseline | "Network setup is normal ({connect} ms vs {base} ms baseline) but time-to-first-byte is {k}× baseline — time is being spent inside the server." |
| `H_NET_SLOW` | connect_ms ≥ 3× baseline | "Connection setup is {k}× slower than usual — likely network path or load-balancer trouble." |
| `H_5XX` | dominant HTTP_UNEXPECTED_STATUS 5xx | "The server is responding with {status} errors — the application or an upstream dependency is failing." |
| `H_CONTRACT` | ASSERTION_FAILED with 2xx | "The endpoint is up but its response no longer matches expectations ({assertion})." |
| `H_TLS_CERT` | TLS_CERT_INVALID | "The certificate failed verification ({reason}) — check expiry and hostname." |

Every hint lists the run IDs it's based on. 🧑‍💻 Writing two of these rules + tests is a learner-owned piece in M5.
