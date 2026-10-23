import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api import auth, incidents, monitors, system
from app.api.csrf import OriginCheckMiddleware
from app.api.errors import install_error_handlers
from app.api.throttle import LoginThrottle
from app.config import get_settings
from app.db.session import SessionLocal
from app.probe.ssrf import SsrfPolicy
from app.worker.loop import Worker

# The SPA is same-origin and ships no third-party scripts, so the policy is strict.
APP_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
# The generated API docs page (FastAPI's Swagger UI) loads its script and styles from a CDN and
# runs an inline script, so it needs a looser policy. It is an opt-in developer page (EXPOSE_DOCS).
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        csp = DOCS_CSP if request.url.path == "/api/docs" else APP_CSP
        response.headers.setdefault("Content-Security-Policy", csp)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA, with index.html fallback for client-side routes."""
    root = static_dir.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        if path.startswith("api/") or path in ("healthz", "readyz"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (root / path).resolve()
        # Only serve files that are inside the static root (no ../ escapes).
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """The worker runs inside the API process: it starts with the app and stops with it."""
    cfg = get_settings()
    if not cfg.run_worker:
        yield
        return
    worker = Worker(
        SessionLocal,
        policy=SsrfPolicy.from_settings(cfg.ssrf_allow_private_cidrs, cfg.allowed_ports),
        batch_size=cfg.batch_size,
        poll_interval_s=cfg.poll_interval_ms / 1000,
        retention_days=cfg.retention_days,
        shutdown_grace_s=cfg.shutdown_grace_s,
    )
    task = asyncio.create_task(worker.run())
    app.state.worker_task = task  # /readyz reports if it ever stops
    try:
        yield
    finally:
        worker.request_stop()
        await task


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    app = FastAPI(
        title="SignalNest",
        lifespan=_lifespan,
        docs_url="/api/docs" if settings.expose_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.expose_docs else None,
    )
    app.state.login_throttle = LoginThrottle(settings.login_max_failures, settings.login_window_s)
    install_error_handlers(app)
    app.add_middleware(OriginCheckMiddleware, trusted_origins=settings.trusted_origins)
    app.add_middleware(SecurityHeadersMiddleware)  # added last = outermost: covers every response
    api = APIRouter(prefix="/api/v1")
    for module in (auth, monitors, incidents):
        api.include_router(module.router)
    app.include_router(api)
    app.include_router(system.router)

    if settings.static_dir and (Path(settings.static_dir) / "index.html").is_file():
        _mount_spa(app, Path(settings.static_dir))
    return app


app = create_app()
