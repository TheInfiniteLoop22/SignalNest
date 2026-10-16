import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api import auth, incidents, monitors, system
from app.api.csrf import OriginCheckMiddleware
from app.api.errors import install_error_handlers
from app.config import get_settings
from app.db.session import SessionLocal
from app.probe.ssrf import SsrfPolicy
from app.worker.loop import Worker

SECURITY_HEADERS = {
    # The SPA is same-origin and ships no third-party scripts.
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
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
            return Response(status_code=404)
        candidate = (root / path).resolve()
        # Only serve files that are inside the static root (no ../ escapes).
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """With EMBEDDED_WORKER=true the worker runs inside this process (one-service hosting)."""
    cfg = get_settings()
    if not cfg.embedded_worker:
        yield
        return
    worker = Worker(
        SessionLocal,
        policy=SsrfPolicy.from_settings(
            cfg.allowed_ports, cfg.ssrf_allow_private_cidrs, cfg.allowed_hosts
        ),
        max_concurrency=cfg.max_concurrency,
        poll_interval_s=cfg.poll_interval_ms / 1000,
        claim_batch_max=cfg.claim_batch_max,
        shutdown_grace_s=cfg.shutdown_grace_s,
        alert_max_attempts=cfg.alert_max_attempts,
        retention_days=cfg.retention_days,
        maintenance_interval_s=cfg.maintenance_interval_s,
    )
    task = asyncio.create_task(worker.run())
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
    install_error_handlers(app)
    app.add_middleware(OriginCheckMiddleware, trusted_origins=settings.trusted_origins)
    app.add_middleware(SecurityHeadersMiddleware)
    api = APIRouter(prefix="/api/v1")
    for module in (auth, monitors, incidents):
        api.include_router(module.router)
    app.include_router(api)
    app.include_router(system.router)

    if settings.static_dir and (Path(settings.static_dir) / "index.html").is_file():
        _mount_spa(app, Path(settings.static_dir))
    return app


app = create_app()
