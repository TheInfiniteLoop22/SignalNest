"""Liveness, readiness, metrics and the System status view (docs/14 sections 3-4)."""

import asyncio
import base64
import hmac
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.api.deps import CurrentUser, DbSession, SettingsDep, require_csrf_header
from app.db.session import get_session
from app.observability import metrics

# Unauthenticated, outside /api/v1.
root = APIRouter(include_in_schema=False)
api = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_csrf_header)])


@root.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Deliberately never touches the database, so a database
    outage does not make an orchestrator restart a healthy process."""
    return {"status": "ok"}


@root.get("/readyz")
async def readyz(request: Request) -> Response:
    """Readiness: can we serve? Needs the database within one second."""
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
        metrics.DB_ERRORS.labels("readyz").inc()
        return JSONResponse({"status": "unavailable", "reason": "database"}, status_code=503)
    return JSONResponse({"status": "ready"})


@root.get("/metrics")
async def prometheus(request: Request, settings: SettingsDep) -> Response:
    """Prometheus text format. With METRICS_BASIC_AUTH=user:pass it requires basic auth;
    empty means "internal network only" (the compose file does not publish this route)."""
    expected = settings.metrics_basic_auth
    if expected:
        header = request.headers.get("authorization", "")
        ok = False
        if header.lower().startswith("basic "):
            try:
                supplied = base64.b64decode(header[6:]).decode()
                ok = hmac.compare_digest(supplied, expected)
            except ValueError:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="metrics"'})
    return Response(metrics.render(), media_type="text/plain; version=0.0.4")


@api.get("/status")
async def system_status(session: DbSession, user: CurrentUser) -> dict[str, Any]:
    """Is the monitor itself healthy? Workers, scheduler lag, housekeeping, our own errors."""
    workers = (
        (
            await session.execute(
                text(
                    """
                    SELECT worker_id, started_at, last_seen_at, in_flight, version, last_error,
                           EXTRACT(EPOCH FROM now() - last_seen_at) AS seen_ago_s
                    FROM worker_heartbeats ORDER BY last_seen_at DESC
                    """
                )
            )
        )
        .mappings()
        .all()
    )
    due = (
        (
            await session.execute(
                text(
                    """
                    SELECT count(*) AS due_count,
                           COALESCE(EXTRACT(EPOCH FROM now() - min(s.next_run_at)), 0) AS lag_s
                    FROM monitor_state s JOIN monitors m ON m.id = s.monitor_id
                    WHERE m.is_paused = false AND s.next_run_at <= now()
                      AND (s.lease_owner IS NULL OR s.lease_expires_at < now())
                    """
                )
            )
        )
        .mappings()
        .one()
    )
    leased = await session.scalar(
        text("SELECT count(*) FROM monitor_state WHERE lease_owner IS NOT NULL")
    )
    job = (
        (
            await session.execute(
                text(
                    """
                    SELECT started_at, finished_at, rows_affected, error
                    FROM job_runs WHERE job_name = 'retention'
                    ORDER BY started_at DESC LIMIT 1
                    """
                )
            )
        )
        .mappings()
        .first()
    )
    # Runs the monitor could not judge (our fault, not the target's). A rising count is the
    # signal that health indicators may be stale. Scoped to the caller's monitors.
    inconclusive = (
        (
            await session.execute(
                text(
                    """
                    SELECT count(*) AS n,
                           count(*) FILTER (WHERE r.failure_class = 'BLOCKED_BY_POLICY') AS blocked
                    FROM check_runs r JOIN monitors m ON m.id = r.monitor_id
                    WHERE m.owner_id = :u AND r.outcome = 'INCONCLUSIVE'
                      AND r.started_at > now() - interval '1 hour'
                    """
                ),
                {"u": user.id},
            )
        )
        .mappings()
        .one()
    )
    return {
        "workers": [
            {
                "worker_id": w["worker_id"],
                "started_at": w["started_at"],
                "last_seen_at": w["last_seen_at"],
                "seen_ago_s": float(w["seen_ago_s"]),
                "alive": float(w["seen_ago_s"]) < 30,
                "in_flight": w["in_flight"],
                "version": w["version"],
                "last_error": w["last_error"],
            }
            for w in workers
        ],
        "scheduler": {
            "due_count": int(due["due_count"]),
            "lag_seconds": float(due["lag_s"]),
            "leased_count": int(leased or 0),
        },
        "retention": dict(job) if job else None,
        "inconclusive_last_hour": {
            "count": int(inconclusive["n"]),
            "blocked_by_policy": int(inconclusive["blocked"]),
        },
    }
