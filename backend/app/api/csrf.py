"""Cross-site request forgery guard for the cookie-authenticated API.

Browsers attach an `Origin` header to every cross-origin state-changing request and page
scripts cannot forge it. A POST/PATCH/DELETE whose Origin is not this app (or a configured
trusted origin) is refused. Requests with no Origin come from non-browser clients such as curl;
they cannot ride on a victim's cookie, so they are allowed. This sits on top of SameSite=Lax.
"""

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api.errors import PROBLEM_BASE

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def origin_allowed(origin: str | None, host: str | None, trusted: frozenset[str]) -> bool:
    if origin is None:
        return True
    if origin in trusted:
        return True
    parts = urlsplit(origin)
    return bool(parts.scheme in ("http", "https") and host and parts.netloc == host)


class OriginCheckMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, trusted_origins: str = "") -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.trusted = frozenset(o.strip().rstrip("/") for o in trusted_origins.split(",") if o)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
            if not origin_allowed(
                request.headers.get("origin"), request.headers.get("host"), self.trusted
            ):
                return JSONResponse(
                    {
                        "type": PROBLEM_BASE + "cross-origin",
                        "title": "Forbidden",
                        "status": 403,
                        "detail": "cross-origin request refused",
                        "instance": request.url.path,
                    },
                    status_code=403,
                    media_type="application/problem+json",
                )
        return await call_next(request)
