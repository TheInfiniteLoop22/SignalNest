# 09 — Security & Threat Model

## 1. Assets
1. The **network position** of the worker/API host (cloud metadata service, internal services, localhost admin ports).
2. User accounts and sessions.
3. Monitor configs (may reveal private infrastructure) and stored responses.
4. Availability of SignalNest itself.
5. Third parties' availability (we must not become a DoS tool).

## 2. Threats (STRIDE-lite) and controls

| # | Threat | Example | Control | Residual risk |
|---|---|---|---|---|
| T1 | **SSRF** | Monitor URL `http://169.254.169.254/latest/meta-data/` to steal cloud credentials; `http://localhost:5432` port scanning | Resolve-validate-pin guard (§3); port allow-list; no redirects; snippet size cap | Bugs in IP classification; new address formats; mitigated by tests + IMDSv2 on AWS |
| T2 | **DNS rebinding** | Hostname resolves public at create time, private at probe time | Re-resolve & validate on *every* attempt; connect to the validated IP only | Very low TTL tricks between resolve and connect are defeated by pinning |
| T3 | **Abuse as DoS tool** | 1000 monitors at 30 s against a victim | Per-user cap 50 monitors (env), min interval 30 s, per-host cap (≤ 5 monitors per user per hostname), registration can be disabled, response read cap | A determined attacker with many accounts; mitigate by disabling public registration on the demo |
| T4 | **IDOR** | `GET /monitors/{someone-else's-id}` | Every query filtered by `owner_id`; 404 not 403; tests for every endpoint | Missed endpoint → dedicated test that iterates all routes with a second user |
| T5 | **Credential attacks** | brute force, credential stuffing | argon2id; login rate limit per email+IP; generic error messages; constant-time compare | No MFA in MVP |
| T6 | **Session theft / CSRF** | XSS steals token; cross-site POST | httpOnly + Secure + SameSite=Lax cookie; custom header requirement for unsafe methods; CORS same-origin only | — |
| T7 | **Stored XSS** | Response snippet or monitor name containing `<script>` | React escapes by default; never `dangerouslySetInnerHTML`; CSP `default-src 'self'` | — |
| T8 | **Header injection / secrets leakage** | User sets `Host`/`Authorization`; stored responses contain `Set-Cookie` | Header denylist and validation; response-header allow-list; no secret headers in MVP | Users may put tokens in query strings → warn in UI; redact query values in logs |
| T9 | **Resource exhaustion of SignalNest** | Huge response bodies, slowloris targets | 64 KB read cap; overall deadline; semaphore bound; lease timeout | — |
| T10 | **Secrets in repo/images** | committed `.env` | `.gitignore`, `gitleaks` in CI, env-only config, `.env.example` placeholders | — |
| T11 | **Vulnerable dependencies** | — | `pip-audit` + `npm audit --omit=dev` in CI; Dependabot | Zero-days |
| T12 | **Metrics/docs exposure** | `/metrics` reveals monitor ids | Bind `/metrics` to internal network or basic auth; docs off in prod | — |

## 3. SSRF guard specification (`app/probe/ssrf.py`)

**Allowed schemes:** `http`, `https`. **Rejected:** userinfo in URL, empty host, ports outside allow-list (default `80,443,8000-8999`, env-configurable).

**Hostname rules:** reject `localhost`, `*.localhost`, `*.local`, `*.internal`, and names without a dot (unless in `ALLOWED_HOSTS` for dev). Normalise IDNA, strip trailing dot, lower-case.

**IP rules — reject if any resolved address is not globally routable.** Use `ipaddress` and check explicitly (don't rely only on `is_global`; know what each covers):

| Range | Why |
|---|---|
| 0.0.0.0/8, 127.0.0.0/8, ::1, :: | this host / loopback |
| 10/8, 172.16/12, 192.168/16, fc00::/7 | private |
| 169.254/16, fe80::/10 | link-local, **cloud metadata (169.254.169.254)** |
| 100.64/10 | carrier-grade NAT |
| 192.0.0/24, 192.0.2/24, 198.18/15, 198.51.100/24, 203.0.113/24, 2001:db8::/32 | special/test/benchmark |
| 224/4, ff00::/8, 240/4, 255.255.255.255 | multicast/reserved/broadcast |
| ::ffff:0:0/96 | IPv4-mapped IPv6 → unwrap and re-check the IPv4 |
| 64:ff9b::/96, 2002::/16 | NAT64 / 6to4 embedding IPv4 → unwrap or reject |

Also reject alternative IPv4 notations parsed by some libraries (`http://2130706433/`, `http://0x7f.1/`, `http://017700000001/`) — parse the host strictly; if it isn't a valid dotted-quad, IPv6 literal, or DNS name, reject.

**Pinning:** connect to the validated IP; set `Host` and SNI to the original hostname; TLS verification against the hostname. No proxy env vars honoured (`trust_env=False`).

**Dev/test override:** `SSRF_ALLOW_PRIVATE_CIDRS` (comma-separated) so Faultline on the docker network is reachable in dev. Must be empty in production; app logs a loud warning at startup if set.

**Tests (table-driven, ≥ 40 cases):** every row above, each encoding trick, IPv6 forms, mixed answers (one public + one private → reject), rebinding simulation via fake resolver returning different answers per call.

**Defence in depth (deployment):** on AWS require IMDSv2; firewall egress from the worker to the internet only (if the platform allows); run containers as non-root.

## 4. Data protection
- Passwords: argon2id. Session tokens: SHA-256 hashed at rest.
- Logs never contain cookies, passwords, request bodies, or full URLs with query strings (query values redacted to `***`).
- `DELETE /monitors` cascades runs; account deletion (next iteration) cascades everything.

## 5. Headers & browser security
`Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'` (tighten if possible), `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `X-Frame-Options: DENY`, HSTS in production.

## 6. Security checklist per PR
- [ ] New query on user data filters by `owner_id`?
- [ ] New outbound request goes through `ssrf.py`?
- [ ] New input validated in Pydantic **and** constrained in DB where sensible?
- [ ] Anything logged that could be secret?
- [ ] Test for the unhappy path added?

## 7. Control review at v1.0.0 (2026-10-15)

Each threat from section 2 against what is actually in the repository. "Evidence" is a test or
file you can run/read; "Gap" is honest.

| # | Control claimed | Evidence | Status / gap |
|---|---|---|---|
| T1 SSRF | resolve-validate-pin; ports; no redirects; body cap | `probe/ssrf.py`; `test_ssrf.py` (48 IP + 30 URL cases), `test_pinning.py` (pinning, rebinding, mixed answers, TLS by IP); create-time 422 in `test_api.py`; `httpx` import confined to `app/probe` (test) | Met. Residual: a public IP that itself proxies inward; `ALLOWED_HOSTS` / `SSRF_ALLOW_PRIVATE_CIDRS` bypass the guard by design (dev only; forced empty in `docker-compose.prod.yml`; startup warning) |
| T2 DNS rebinding | re-resolve every attempt, connect to validated IP | `test_dns_rebinding_public_then_private_is_blocked` | Met |
| T3 Abuse as DoS tool | per-user cap 50, per-host cap 5, min interval 30 s, registration switch, read cap | `test_per_user_and_per_host_caps`, `test_minimum_interval_is_enforced`; 64 KB read cap in engine; prod registration closed | Met for one operator. A determined attacker with many accounts is not stopped (demo should keep registration closed) |
| T4 IDOR | `owner_id` filter, 404 not 403 | route-crawler test over the OpenAPI document | Met. The crawler only covers routes with an id parameter; list endpoints are covered by explicit assertions |
| T5 Credential attacks | argon2id; login limit per email+IP; generic errors; constant-time | `test_login_errors_are_generic_and_rate_limited`; dummy-hash verify for unknown users | Met. No MFA. Limiter is per process (stated) |
| T6 Session theft / CSRF | httpOnly+Secure+SameSite=Lax; header on unsafe methods; same-origin | `test_session_cookie_flags_and_hashed_token`, `test_unsafe_requests_need_the_csrf_header`; no CORS middleware installed | Met. Sessions are not rotated on privilege change (there is none yet) |
| T7 Stored XSS | text-only rendering; CSP | App test feeds `<img onerror>`/`<script>` and asserts text; `SECURITY_HEADERS` middleware | Met. CSP allows inline *styles* (Recharts) but not inline scripts |
| T8 Header injection / secret leakage | header denylist; response-header allow-list; query values redacted in logs | `test_monitor_validation` (Host/Authorization/Proxy-*/CRLF), `test_response_headers_are_allow_listed`, `redact_url`, request log has route template only | Met. Users can still put secrets in query strings in the monitor URL (the URL is stored; it is not logged) |
| T9 Resource exhaustion | read cap; deadline; semaphore; lease | engine tests (`/big`, `/drip`, `/hang`); `test_concurrency_is_bounded` | Met |
| T10 Secrets in repo/images | `.gitignore`, env-only config, scanning | `.env*` ignored (examples allowed); `gitleaks` job in CI (added at v1.0) | Met; history was scanned by the CI job on first run |
| T11 Vulnerable dependencies | `pip-audit`, `npm audit`, Dependabot | `security` job in CI; `.github/dependabot.yml` (added at v1.0). On 2026-10-15: pip-audit clean; npm audit high findings in `react-router` fixed by 6.30.6 | **Open:** 2 moderate `react-router` advisories (backslash open redirect, SSR hydration) need the breaking v7 upgrade; not reachable (no SSR, no user-controlled navigation targets) |
| T12 Metrics/docs exposure | `/metrics` internal or basic auth; docs off in prod | `test_metrics_basic_auth`; Caddyfile 404s `/metrics`, `/readyz`, docs; `EXPOSE_DOCS=false` | Met locally; not verified on a public host |

**Review findings fixed in this pass:** the CI had no `security` job and no Dependabot config
although the testing plan lists them (added); three high `npm audit` findings (fixed by upgrading
`react-router-dom` 6.26.2 -> 6.30.6); `.env.prod.example` was being git-ignored by the `.env.*`
rule (now allowed).

**Known, accepted, not fixed:** in-process rate limiting; no MFA; `style-src 'unsafe-inline'`;
sessions are valid for 7 days absolute with no idle timeout; no account deletion or password change
endpoint (so "revoke all sessions on password change" has no trigger yet); logs may contain
`monitor_id` and `user`-correlated request ids (no PII beyond that).
