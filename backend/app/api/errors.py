"""RFC 9457 problem+json responses and request-id middleware."""

import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

PROBLEM_BASE = "https://signalnest.dev/problems/"
_TITLES = {
    400: "Bad request",
    401: "Authentication required",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    422: "Validation failed",
    429: "Too many requests",
    503: "Service unavailable",
}


class Problem(HTTPException):
    """Raise anywhere in a handler to produce a problem+json response."""

    def __init__(
        self,
        status: int,
        slug: str,
        detail: str,
        *,
        title: str | None = None,
        errors: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status, detail=detail, headers=headers)
        self.slug = slug
        self.title = title or _TITLES.get(status, "Error")
        self.errors = errors


def _body(
    request: Request,
    status: int,
    slug: str,
    title: str,
    detail: str,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": PROBLEM_BASE + slug,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "request_id": getattr(request.state, "request_id", None),
    }
    if errors:
        body["errors"] = errors
    return body


def _response(body: dict[str, Any], headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        body, status_code=body["status"], media_type="application/problem+json", headers=headers
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid[:64]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


def install_error_handlers(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(Problem)
    async def _problem(request: Request, exc: Problem) -> JSONResponse:
        return _response(
            _body(request, exc.status_code, exc.slug, exc.title, str(exc.detail), exc.errors),
            exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        slug = {401: "unauthenticated", 404: "not-found", 405: "method-not-allowed"}.get(
            exc.status_code, "error"
        )
        title = _TITLES.get(exc.status_code, "Error")
        return _response(
            _body(request, exc.status_code, slug, title, str(exc.detail)),
            getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in e["loc"] if p not in ("body", "query", "path")),
                "message": str(e["msg"]),
            }
            for e in exc.errors()
        ]
        first = errors[0]["message"] if errors else "invalid request"
        return _response(_body(request, 422, "validation", "Validation failed", first, errors))
