from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api import auth, incidents, monitors, probe, runs
from app.api.errors import install_error_handlers
from app.api.ratelimit import FixedWindowLimiter
from app.config import get_settings

SECURITY_HEADERS = {
    # The SPA is same-origin and ships no third-party scripts. Inline styles are allowed
    # because the charting library sets style attributes.
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
        if path.startswith("api/"):  # unknown API paths stay JSON 404s
            return Response(status_code=404)
        candidate = (root / path).resolve()
        # Only serve files that are inside the static root (no ../ escapes).
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SignalNest",
        docs_url="/api/docs" if settings.expose_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.expose_docs else None,
    )
    install_error_handlers(app)
    app.add_middleware(SecurityHeadersMiddleware)
    # In-process fixed windows (see ratelimit.py for the multi-replica limitation).
    app.state.limiters = {
        "login": FixedWindowLimiter(limit=5, window_s=15 * 60),
        "probe_test": FixedWindowLimiter(limit=6, window_s=60),
        "check_now": FixedWindowLimiter(limit=1, window_s=10),
    }
    api = APIRouter(prefix="/api/v1")
    for module in (auth, monitors, runs, probe, incidents):
        api.include_router(module.router)
    app.include_router(api)

    if settings.static_dir and (Path(settings.static_dir) / "index.html").is_file():
        _mount_spa(app, Path(settings.static_dir))
    return app


app = create_app()
