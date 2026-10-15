from fastapi import APIRouter, FastAPI

from app.api import auth, incidents, monitors, probe, runs
from app.api.errors import install_error_handlers
from app.api.ratelimit import FixedWindowLimiter
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SignalNest",
        docs_url="/api/docs" if settings.expose_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.expose_docs else None,
    )
    install_error_handlers(app)
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
    return app


app = create_app()
