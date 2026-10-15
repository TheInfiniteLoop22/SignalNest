# 13 — Repository & DevOps

## 1. Layout (grows milestone by milestone — don't create folders before they're needed)

```
signalnest/
├── CLAUDE.md
├── README.md
├── CHANGELOG.md
├── .env.example
├── .gitignore
├── docker-compose.yml           # dev: db, api, worker, faultline, web (vite dev server)
├── docker-compose.prod.yml      # prod: db, api(+static web), worker, caddy
├── Dockerfile                   # multi-stage: python base → api/worker target; node build → static
├── Caddyfile
├── pyproject.toml
├── alembic.ini
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory
│   │   ├── cli.py               # dev CLI (probe once, seed)
│   │   ├── config.py            # pydantic-settings
│   │   ├── api/                 # routers: auth, monitors, runs, incidents, probe, system
│   │   ├── services/            # use-cases + transactions
│   │   ├── probe/               # engine.py, classify.py, assertions.py, ssrf.py, types.py
│   │   ├── incidents/           # state_machine.py, hints.py, evidence.py
│   │   ├── worker/              # main.py, loop.py, claim.py, complete.py, shutdown.py, retention.py
│   │   ├── db/                  # base.py, session.py, models/
│   │   └── observability/       # logging.py, metrics.py, middleware.py
│   ├── migrations/              # alembic versions
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── conftest.py          # pg fixture, faultline fixture, fake resolver, trustme certs
├── faultline/
│   ├── app.py
│   ├── socket_faults.py
│   └── tests/
├── frontend/
│   ├── src/ (api/, components/, pages/, lib/)
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
├── docs/  (this pack + adr/ + LEARNING_LOG.md + learning/)
└── .github/workflows/ci.yml, deploy.yml
```

## 2. `.env.example`

```dotenv
# --- core ---
ENV=dev                                   # dev | test | prod
DATABASE_URL=postgresql+asyncpg://signalnest:change-me@db:5432/signalnest
SECRET_KEY=change-me-32-bytes-min         # used for signing/csrf helpers; NOT a session token
ALLOW_REGISTRATION=true
SESSION_TTL_HOURS=168
LOG_LEVEL=INFO

# --- worker ---
WORKER_ID=                                # default: hostname-pid
MAX_CONCURRENCY=20
POLL_INTERVAL_MS=1000
CLAIM_BATCH_MAX=50
SHUTDOWN_GRACE_S=20
RUN_RETENTION_DAYS=14

# --- limits ---
MAX_MONITORS_PER_USER=50
MIN_INTERVAL_S=30                         # tests may lower this
ALLOWED_PORTS=80,443,8000-8999

# --- SSRF (dev/test only; must be empty in prod) ---
SSRF_ALLOW_PRIVATE_CIDRS=

# --- ops ---
METRICS_BASIC_AUTH=                       # user:pass, empty = internal only
EXPOSE_DOCS=false

# --- stretch ---
LLM_ENABLED=false
LLM_API_KEY=
```

## 3. Docker
- **Multi-stage `Dockerfile`:** `python:3.12-slim` base → install deps from lockfile → copy app → `USER app` (non-root). Targets: `api` (`uvicorn app.main:app`), `worker` (`python -m app.worker.main`). Frontend built in a `node:20` stage and copied into the api image's `/static`.
- **Dev compose:** bind-mount source, `--reload` for api, Postgres with named volume and `pg_isready` healthcheck, Faultline on the internal network, `SSRF_ALLOW_PRIVATE_CIDRS=172.16.0.0/12` (docker network) **only here**.
- **Graceful shutdown:** `stop_grace_period: 30s` for the worker; `exec` form `CMD` so signals reach Python.
- Migrations: a one-shot `migrate` service (`alembic upgrade head`) that api/worker depend on with `condition: service_completed_successfully`.

## 4. CI (`.github/workflows/ci.yml`)
Jobs as in `11_TESTING_PLAN.md` §5. Postgres as a service container. Cache pip/uv and npm. Run on PRs and pushes to `main`. Upload coverage report as an artifact. Dependabot for pip, npm and GitHub Actions.

## 5. Deployment (M9 — see ADR-012)
1. VM (smallest instance that fits Postgres + 2 Python processes; measure memory in compose with `docker stats` first).
2. Firewall: 22 from your IP only; 80/443 public; nothing else.
3. Install Docker; clone repo (or pull images from GHCR built in CI).
4. `/opt/signalnest/.env` with `chmod 600`, owned by deploy user.
5. `docker compose -f docker-compose.prod.yml up -d`; Caddy obtains TLS certs automatically for your domain.
6. Cron: nightly `pg_dump | gzip` → object storage (S3/Blob/GCS) with lifecycle rule; **monthly restore drill** into a scratch container.
7. AWS-specific: enforce IMDSv2 (`HttpTokens=required`) — defence in depth for SSRF.

## 6. Git workflow
- Branch per issue: `m4/claim-query`. Small PRs (< 400 lines diff). PR template: what/why, how tested (paste output), docs updated?, security checklist (`09` §6).
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`).
- Tag milestones: `m4-done`, final `v1.0.0`.
