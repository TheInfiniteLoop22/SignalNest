# syntax=docker/dockerfile:1
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend ./
RUN npm run build

FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONPATH=/srv/backend:/srv
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY alembic.ini ./
COPY backend ./backend
COPY faultline ./faultline
RUN useradd --system --uid 10001 app && chown -R app /srv
USER app
ENV PATH="/srv/.venv/bin:$PATH"

FROM base AS faultline
EXPOSE 8001
CMD ["sh", "-c", "exec uvicorn faultline.app:app --host 0.0.0.0 --port ${PORT:-8001}"]

# Final stage, so it is the default build target (hosting platforms build this one): create the
# tables, then serve the API and UI. The background worker runs inside this same process.
FROM base AS app
ENV STATIC_DIR=/srv/static
COPY --from=web --chown=app /web/dist /srv/static
EXPOSE 8000
# exec so SIGTERM reaches uvicorn and the worker can stop cleanly.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
