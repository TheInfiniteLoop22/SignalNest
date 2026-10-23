# SignalNest codebase guide

A map of the repository: what each file does and how the pieces connect. Read it top to bottom once,
then use it as a reference.

## 1. What the app does

SignalNest is an uptime monitor. A user registers, adds **monitors** (a URL plus rules), and a
background **worker** probes each URL on a schedule. Every probe is stored as a **check run** with
phase timings (DNS, connect, TLS, first byte, total). After N failures in a row an **incident** opens
(and an optional webhook fires); the first success resolves it. The React UI shows status, charts
and incidents.

It runs as **one process**: FastAPI serves the JSON API and the built React app, and the worker runs
in the same process as a background task. State lives in PostgreSQL.

## 2. How a request and a check flow through the code

```
Browser (React)                         Postgres
   │  /api/v1/...  (cookie session)        ▲
   ▼                                       │
FastAPI  main.py ──► middleware (CSRF origin check, security headers)
   │                 └► api/*.py routers ──► db/models.py (SQLAlchemy)
   │
   └── lifespan starts ──► worker/loop.py  (every ~1 s)
                              │  finds due monitors (db)
                              ▼
                         probe/engine.py ──► probe/ssrf.py (is this target allowed?)
                              │             probe/classify.py (what kind of failure?)
                              ▼
                         incidents/state_machine.py (open / resolve / nothing)
                              │
                              ▼
                         save CheckRun + update Monitor + Incident (one commit)
                              │
                              └─► worker/webhook.py (alert, through the same safe engine)
```

**Creating a monitor:** `POST /api/v1/monitors` → `api/monitors.py` validates the URL with the SSRF
policy (`probe/ssrf.py` plus DNS resolution), then inserts a `Monitor` row whose `next_run_at` is
"now". The worker picks it up within a second.

**One check:** the worker calls `run_probe` (`probe/engine.py`). The engine resolves DNS, rejects
non-public addresses, connects to the *already-checked IP*, times each phase, and judges the
response. The worker passes the result to `evaluate` (`incidents/state_machine.py`), then writes the
run, status change and incident in a single transaction and schedules the next check.

## 3. Repository layout

```
backend/app/        the Python application
backend/migrations/ Alembic database migrations
backend/tests/      unit, probe and integration tests
faultline/          a fake, breakable website used for demos and tests
frontend/           the React single-page app
screenshots/        images used by the README
docs/               documentation (this file)
Dockerfile, docker-compose.yml, render.yaml   packaging and deployment
.github/workflows/ci.yml                      continuous integration
pyproject.toml, uv.lock                       Python dependencies and tool config
alembic.ini                                   Alembic settings
.env.example                                  every setting, with safe defaults
```

## 4. Backend (`backend/app/`)

### Entry point and configuration
| File | What it does |
|---|---|
| `main.py` | Builds the FastAPI app. Registers the API routers, the CSRF and security-header middleware, error handlers and the SPA (serves the built React files, falling back to `index.html` for client-side routes). Its `lifespan` function starts the worker when the app starts and stops it cleanly on shutdown. Also sets the Content-Security-Policy. |
| `config.py` | `Settings` (pydantic-settings). Every tunable comes from environment variables: database URL, worker batch size, retention, SSRF allow-list, allowed ports, cookie flags, login-throttle limits, monitor caps. Normalises `postgres://` URLs to the async driver. |
| `security.py` | Password hashing with argon2id, constant-time verification (a dummy hash is checked for unknown emails so timing does not reveal which accounts exist), and session-token helpers (random token for the cookie, only its SHA-256 stored). |
| `cli.py` | Developer command line: seed a user and monitor, or run one probe from the terminal. |

### Database (`db/`)
| File | What it does |
|---|---|
| `base.py` | The SQLAlchemy declarative base. |
| `session.py` | Creates the async engine and `SessionLocal` session factory from settings. |
| `models.py` | The five tables: **User**, **Session** (login sessions, token stored hashed), **Monitor** (URL, interval, timeout, expected status, body match, failure threshold, webhook, current status, `next_run_at`, consecutive failures), **CheckRun** (one probe result with all timings and failure class), **Incident** (open/resolved, cause). |

Schema changes live in `backend/migrations/versions/` (`0001_initial.py`, then
`0002_check_runs_started_at_index.py`); `migrations/env.py` wires Alembic to the app's settings and
models. The Docker image runs `alembic upgrade head` before starting the server.

### HTTP API (`api/`)
All routes are mounted under `/api/v1`, except health checks.

| File | What it does |
|---|---|
| `auth.py` | `POST /auth/register`, `/auth/login`, `/auth/logout`, `GET /auth/me`. Login sets an `HttpOnly`, `SameSite=Lax` session cookie and consults the throttle. |
| `monitors.py` | CRUD for monitors plus `GET /monitors/{id}/runs`. Enforces per-user caps and minimum interval, and validates URLs and webhook URLs against the SSRF policy. Computes the 24 h uptime figure. |
| `incidents.py` | `GET /incidents`, newest and open first. |
| `system.py` | `/healthz` (process is alive) and `/readyz` (database reachable and worker still running). |
| `schemas.py` | Pydantic request and response models: the API contract and its validation rules. |
| `deps.py` | Shared FastAPI dependencies: DB session, settings, `current_user` (looks up the session cookie), SSRF policy, login throttle. |
| `csrf.py` | Middleware that refuses state-changing requests whose `Origin` header is a foreign site. |
| `throttle.py` | In-memory login throttle: too many failures for one email locks it for a while; unknown emails are treated the same. |
| `errors.py` | Turns validation errors into the simple `{"detail": "..."}` shape the frontend shows. |

### Probing (`probe/`): the core of the product
| File | What it does |
|---|---|
| `types.py` | Plain data types: `ProbeRequest`, `ProbeResult`, `Outcome` (PASS/FAIL) and `FailureClass` (DNS, CONNECT, TLS, TIMEOUT, BAD_RESPONSE, BLOCKED, ERROR). No I/O. |
| `ssrf.py` | The SSRF guard. Only http/https, no credentials in the URL, only allowed ports, and every resolved IP must be public. Also unwraps IPv4-mapped IPv6 and NAT64 addresses so `::ffff:127.0.0.1` cannot sneak through. |
| `engine.py` | `run_probe`: resolve DNS, apply the SSRF policy, connect to the **pinned, already-validated IP** (defeats DNS rebinding), time each phase, read only the first 64 KB of the body, judge status and body. Never touches the database. |
| `classify.py` | Pure function mapping an exception to a `FailureClass`; unknown errors become `ERROR`, never a wrong specific blame. |

### Incidents (`incidents/`)
| File | What it does |
|---|---|
| `state_machine.py` | `evaluate(...)`: pure decision function. Opens an incident after `failure_threshold` consecutive failures, resolves on the first pass. Easy to test because it has no I/O. |

### Worker (`worker/`)
| File | What it does |
|---|---|
| `loop.py` | `Worker`: every second finds monitors whose `next_run_at` has passed, starts each check as its own task (so one slow site never delays others), limits concurrency, tolerates database outages, runs hourly cleanup of old runs and expired sessions, and shuts down gracefully. `record_result` writes the run, status and incident in one commit. |
| `webhook.py` | Builds and sends the alert message (JSON; works with Slack and Discord incoming webhooks) when an incident opens or resolves. It goes through the probe engine, so webhook URLs get the same SSRF protection. |

## 5. Faultline (`faultline/`)

A tiny FastAPI site you can **break on purpose**, so you can see SignalNest react.

| File | What it does |
|---|---|
| `app.py` | Endpoints like `/ok`, `/status/{code}`, `/hang`, `/big`, `/drip`, `/redirect`, and `/api/orders`, whose behaviour is switched by `POST /scenario` (`healthy`, `slow_db`, `outage`, `broken_contract`, `hang`). |
| `socket_faults.py` | Raw-socket misbehaviour HTTP frameworks cannot express (connection reset, bad TLS and similar), used by the tests. |

## 6. Frontend (`frontend/`)

React 18 + TypeScript, built with Vite, data fetched with TanStack Query, routing with React Router.

| File | What it does |
|---|---|
| `index.html`, `public/` | HTML shell, favicon and the SVG illustrations (`logo`, `hero`, `empty`, `ok`). |
| `vite.config.ts` | Dev server on 5173 proxying `/api` to the backend (so cookies are same-origin), plus the Vitest setup. |
| `src/main.tsx` | Mounts the app with the query client and router. |
| `src/App.tsx` | Route table. `RequireAuth` redirects signed-out visitors to `/login`; a global event clears cached data when any request returns 401. |
| `src/api/client.ts` | `api()` wrapper around `fetch`: same-origin cookies, JSON handling, `ApiError`, and the "session expired" event. |
| `src/api/hooks.ts` | TanStack Query hooks (`useMe`, `useMonitors`, `useMonitor`, `useRuns`, `useIncidents`, save/delete mutations). Pages poll every 10 s. |
| `src/api/types.ts` | TypeScript types mirroring the API schemas. |
| `src/pages/Login.tsx` | Sign-in and registration. |
| `src/pages/Monitors.tsx` | Monitor list with status counters. |
| `src/pages/MonitorDetail.tsx` | One monitor: stats, response-time chart, recent runs with timings, edit and delete. |
| `src/pages/MonitorForm.tsx` | Create and edit form; server-side validation errors (blocked URL and so on) are shown inline. |
| `src/pages/Incidents.tsx` | Incident history. |
| `src/components/` | `Layout` (top bar and logout), `Badge` (status pills; the text label carries the meaning, not just colour), `ResponseChart` (inline SVG line chart), `Common` (loading and error boxes). |
| `src/lib/format.ts` | Formatting helpers for milliseconds, percentages, times, hostnames. |
| `src/styles.css` | All styling, with light and dark themes through CSS variables. |
| `src/App.test.tsx` | Vitest and Testing Library tests of the main user flows. |

In production the Docker build compiles the frontend and the backend serves it from `/srv/static`,
so the browser only ever talks to one origin.

## 7. Tests (`backend/tests/`)

| Folder | What it covers |
|---|---|
| `unit/` | Pure logic: SSRF rules, failure classification, the incident state machine, the login throttle, CSRF origin rules, SPA file serving, request-schema checks. No database. |
| `probe/` | The probe engine against real local servers (timeouts, resets, DNS pinning). |
| `integration/` | The API and worker against a real PostgreSQL: auth flow, monitors CRUD, isolation between users, incident open and resolve, surviving a database outage. `api_support.py` holds shared helpers. |
| `conftest.py` | Fixtures: starts a Faultline server, creates the schema with Alembic, provides a session factory. |

CI (`.github/workflows/ci.yml`) runs lint (ruff), types (mypy, strict on probe, incidents and
worker), unit tests, integration tests against PostgreSQL, the frontend type-check, tests and build,
and a Docker image build.

## 8. Packaging and deployment

| File | What it does |
|---|---|
| `Dockerfile` | Multi-stage: builds the frontend, then a Python base, then a `faultline` target and the final `app` target (runs migrations, then uvicorn, as a non-root user). |
| `docker-compose.yml` | Local stack: Postgres, the app (port 8000) and Faultline (port 8001). Allows the Docker private range for the SSRF guard (dev only). |
| `render.yaml` | Render Blueprint: the app web service, the demo-target web service and a Postgres database, all on free plans. |
| `.env.example` | Every setting with defaults. |
| `pyproject.toml` / `uv.lock` | Dependencies (managed with `uv`) and ruff, mypy and pytest configuration. |

## 9. Security features and where they live

| Feature | File |
|---|---|
| SSRF guard (scheme, credentials, ports, public-IP-only, IP pinning) | `probe/ssrf.py`, `probe/engine.py` |
| Password hashing (argon2id) and uniform login timing | `security.py` |
| Server-side sessions, only a hash of the token stored | `security.py`, `api/auth.py`, `api/deps.py` |
| Login throttling | `api/throttle.py` |
| CSRF origin check and `SameSite` cookie | `api/csrf.py`, `api/auth.py` |
| Security headers and CSP | `main.py` |
| Per-user data isolation | `api/monitors.py`, `api/incidents.py` (every query filters on the owner) |

## 10. Suggested reading order

1. `backend/app/db/models.py`: the data.
2. `backend/app/probe/types.py` then `ssrf.py` then `engine.py`: how one check works.
3. `backend/app/incidents/state_machine.py`: when incidents open and close.
4. `backend/app/worker/loop.py`: how checks are scheduled.
5. `backend/app/api/monitors.py` and `auth.py`: the API surface.
6. `backend/app/main.py`: how it is all assembled.
7. `frontend/src/api/` then `pages/`: how the UI consumes the API.
