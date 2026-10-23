"""Cross-site request forgery guard for the cookie-authenticated API.

Browsers attach an `Origin` header to cross-origin requests and page scripts cannot forge it. A
POST/PATCH/DELETE under /api/ whose Origin is not this app (or a configured trusted origin) is
refused. Requests without an Origin come from non-browser clients such as curl; they cannot ride
on a victim's cookie, so they are allowed. This sits on top of the SameSite=Lax cookie.
"""

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def origin_allowed(origin: str | None, host: str | None, trusted: frozenset[str]) -> bool:
    if origin is None or origin in trusted:
        return True
    parts = urlsplit(origin)
    return bool(parts.scheme in ("http", "https") and host and parts.netloc == host)


class OriginCheckMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, trusted_origins: str = "") -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.trusted = frozenset(o.strip().rstrip("/") for o in trusted_origins.split(",") if o)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
            origin, host = request.headers.get("origin"), request.headers.get("host")
            if not origin_allowed(origin, host, self.trusted):
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)
