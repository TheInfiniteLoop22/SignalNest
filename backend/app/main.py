import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api import auth, incidents, monitors, system
from app.api.errors import install_error_handlers
from app.config import get_settings

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


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    app = FastAPI(
        title="SignalNest",
        docs_url="/api/docs" if settings.expose_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.expose_docs else None,
    )
    install_error_handlers(app)
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
