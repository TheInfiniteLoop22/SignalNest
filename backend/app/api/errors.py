"""Error responses use FastAPI's standard shape: {"detail": "message"}.

Validation errors (a field out of range, a missing field) are reduced to one message naming the
first problem and its field, so the UI always has one readable string to show.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        if not errors:
            return JSONResponse({"detail": "invalid request"}, status_code=422)
        first = errors[0]
        field = ".".join(str(p) for p in first["loc"] if p not in ("body", "query", "path"))
        message = str(first["msg"])
        return JSONResponse(
            {"detail": f"{field}: {message}" if field else message}, status_code=422
        )
