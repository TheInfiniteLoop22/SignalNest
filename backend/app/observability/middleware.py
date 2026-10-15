"""Request id, access log and HTTP metrics in one middleware."""

import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.observability import metrics
from app.observability.logging import request_id_var

log = logging.getLogger("signalnest.http")


def _template(request: Request) -> str:
    if request.scope.get("route") is None:
        return "unmatched"
    path = request.url.path
    for name, value in request.scope.get("path_params", {}).items():
        path = path.replace(str(value), "{" + name + "}", 1)
    return path


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:64]
        request.state.request_id = rid
        token = request_id_var.set(rid)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            elapsed = time.perf_counter() - started
            # Route *template* (/api/v1/monitors/{monitor_id}), never the concrete path: ids
            # in a label would explode the number of series.
            route = _template(request)
            metrics.HTTP_REQUESTS.labels(route, request.method, str(status)).inc()
            metrics.HTTP_DURATION.labels(route, request.method).observe(elapsed)
            if route not in ("/healthz", "/readyz", "/metrics"):
                log.info(
                    "http.request",
                    extra={
                        "method": request.method,
                        "route": route,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 1),
                    },
                )
            request_id_var.reset(token)
