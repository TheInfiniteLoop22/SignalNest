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
├── docs/  (this pack + adr/ + learning/)
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

## 5. Deployment (M9 - see ADR-012)

Target: **one VM** running `docker-compose.prod.yml` (caddy, api, worker, db). The provider is
not fixed by the repo (AWS EC2, Azure VM, GCP Compute Engine or any VPS all work); provider
specific steps are marked. Everything below that does not need a provider was verified locally,
see "What was verified" at the end.

### 5.1 One-time host setup
1. Create the VM (Ubuntu LTS, smallest size that fits Postgres + two Python processes; check with
   `docker stats` first). Attach a static public IP and point the DNS `A`/`AAAA` record of `DOMAIN` at it.
2. **Firewall (provider security group *and* host `ufw`; belt and braces):**

   | Direction | Port | Source | Why |
   |---|---|---|---|
   | in | 22/tcp | your IP only | SSH administration |
   | in | 80/tcp | anywhere | ACME HTTP-01 challenge + redirect to HTTPS |
   | in | 443/tcp | anywhere | the application |
   | in | everything else | - | denied (Postgres 5432, API 8000 and metrics 9100 are never published) |
   | out | 443, 53, 123 | anywhere | image pulls, probes to targets, DNS, NTP |

   ```
   sudo ufw default deny incoming && sudo ufw default allow outgoing
   sudo ufw allow from <YOUR_IP> to any port 22 proto tcp
   sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
   ```
   Note: Docker publishes ports by editing iptables *before* ufw sees them. That is why the
   compose file publishes only caddy's 80/443 and nothing else; do not add `ports:` to `db`.
3. **SSH hardening** (`/etc/ssh/sshd_config.d/10-hardening.conf`): `PasswordAuthentication no`,
   `PermitRootLogin no`, `AllowUsers deploy`; `sudo systemctl reload ssh`. Key-only login for a
   non-root `deploy` user in the `docker` group.
4. Install Docker Engine + compose plugin; `sudo mkdir -p /opt/signalnest && sudo chown deploy: /opt/signalnest`;
   `git clone <repo> /opt/signalnest/app`.
5. **Secrets:** `cp .env.prod.example /opt/signalnest/.env.prod && chmod 600 /opt/signalnest/.env.prod`
   and fill it (`openssl rand -base64 32` for the Postgres password). The file lives only on the
   host, outside the repo and the images; compose reads it with `--env-file`.
6. **AWS only:** require IMDSv2 (`aws ec2 modify-instance-metadata-options --instance-id <id>
   --http-tokens required --http-endpoint enabled`). Defence in depth for SSRF: even a guard bug
   could not read credentials with a plain GET.

### 5.2 Deploy / update
```
cd /opt/signalnest/app && git fetch --tags && git checkout <tag>
C="docker compose -f docker-compose.prod.yml --env-file /opt/signalnest/.env.prod"
$C build && $C run --rm migrate && $C up -d worker && $C up -d api caddy
```
`.github/workflows/deploy.yml` does the same over SSH (manual trigger; needs `DEPLOY_HOST`,
`DEPLOY_USER`, `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` secrets). Images are built **on the host**
from the checked-out tag: simpler and fine for one VM, at the cost of build time and a build
toolchain on production. Pushing images to GHCR from CI is the upgrade when that matters.

Create accounts (registration is closed by default):
`printf '%s\n' '<password>' | $C run --rm -T api python -m app.cli create-user --email you@example.com --password-stdin`

### 5.3 Backups and the restore drill
* `ops/backup.sh` (cron, nightly): `pg_dump --format=custom` -> `BACKUP_DIR`, verified readable
  with `pg_restore --list`, sha256 recorded, pruned after `BACKUP_KEEP_DAYS`, and optionally
  uploaded with `BACKUP_UPLOAD_CMD` (object storage with a lifecycle rule; **provider specific**).
  ```
  15 3 * * * cd /opt/signalnest/app && ENV_FILE=/opt/signalnest/.env.prod ./ops/backup.sh >> /var/log/signalnest-backup.log 2>&1
  ```
* `ops/restore-drill.sh [dump]`: restores into a throwaway Postgres container and checks the
  alembic revision, row counts, no duplicate slots, one open incident per monitor, no orphans and
  that the key indexes exist. Run it monthly and after any schema change; record the timings below.
* **Recovery procedure after losing the VM:** new VM (5.1) -> clone repo -> restore `.env.prod` from your
  password manager -> `$C up -d db` -> `docker compose exec -T db pg_restore -U $POSTGRES_USER -d $POSTGRES_DB --clean --if-exists --no-owner < <dump>`
  -> `$C run --rm migrate` -> `$C up -d`. Worst-case data loss is one backup interval (24 h with the
  nightly schedule; the loss is check history, which is re-created by new probes).

### 5.4 What was verified
Run on a developer machine (Docker Desktop) with `DOMAIN=localhost`, 2026-10-15; **not** on a
real cloud VM, not against a public domain:

| Check | Result |
|---|---|
| `docker compose -f docker-compose.prod.yml up -d --build` | db, api healthy; worker and caddy up; migrate exits 0 |
| `https://localhost/` | 200 with CSP, HSTS, `X-Frame-Options: DENY`, `nosniff`; `Server` header removed |
| `http://localhost/` | 308 redirect to https |
| `/metrics`, `/readyz`, `/api/docs`, `/api/openapi.json` via caddy | 404 (internal only) |
| Registration | 403 `registration-disabled`; account created via `create-user` instead |
| Login cookie | `HttpOnly`, `Secure`, `SameSite=Lax` |
| SSRF at create | `http://169.254.169.254/` -> 422 |
| Probe from the container | `https://example.com/` -> PASS, recorded by the worker |
| Containers | api and worker run as uid 10001; root filesystem read-only (`touch` fails) |
| `ops/backup.sh` then `ops/restore-drill.sh` | dump 25 916 bytes; drill passed all checks; restore 0 s, total 5 s (tiny dataset: 1 user, 1 monitor, 2 runs, so this is **not** a production recovery-time estimate) |

Not verified (needs a provider account/domain): ACME certificate issuance for a public name,
firewall rules, SSH hardening, IMDSv2, object-storage upload, the GitHub deploy workflow.

## 6. Git workflow
- Branch per issue: `m4/claim-query`. Small PRs (< 400 lines diff). PR template: what/why, how tested (paste output), docs updated?, security checklist (`09` §6).
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`).
- Tag milestones: `m4-done`, final `v1.0.0`.
