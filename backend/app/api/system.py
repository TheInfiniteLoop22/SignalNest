"""Liveness and readiness."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.db.session import get_session

router = APIRouter(include_in_schema=False)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Never touches the database, so a database outage does
    not make an orchestrator restart a healthy process."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> Response:
    """Readiness: can we serve? Needs the database within one second, and the worker running."""
    get = request.app.dependency_overrides.get(get_session, get_session)
    try:
        async with asyncio.timeout(1.0):
            agen = get()
            session = await agen.__anext__()
            try:
                await session.execute(text("SELECT 1"))
            finally:
                await agen.aclose()
    except Exception:
        return JSONResponse({"status": "unavailable", "reason": "database"}, status_code=503)
    worker_task = getattr(request.app.state, "worker_task", None)
    if worker_task is not None and worker_task.done():  # the loop should never end by itself
        return JSONResponse({"status": "unavailable", "reason": "worker"}, status_code=503)
    return JSONResponse({"status": "ready"})
