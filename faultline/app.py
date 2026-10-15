"""Faultline: a fault-injection target. Deterministic by default; only state is the scenario."""

import asyncio
import json
import random
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Faultline")

_flaky_counters: dict[int, int] = {}
_scenario: dict[str, Any] = {"mode": "healthy"}


@app.get("/ok")
async def ok() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/slow")
async def slow(ms: int = 1500) -> dict[str, str]:
    await asyncio.sleep(ms / 1000)
    return {"status": "ok", "slept_ms": str(ms)}


@app.get("/hang")
async def hang() -> None:
    """Accept the request and never respond."""
    await asyncio.sleep(3600)


@app.get("/status/{code}")
async def status(code: int) -> Response:
    return Response(content=f"status {code}", status_code=code)


@app.get("/json")
async def json_body(status: str = "down") -> dict[str, str]:
    return {"status": status}


@app.get("/big")
async def big(kb: int = 500) -> Response:
    return Response(content=b"x" * (kb * 1024), media_type="text/plain")


@app.get("/redirect")
async def redirect(to: str = "/ok") -> RedirectResponse:
    return RedirectResponse(to, status_code=302)


@app.get("/drip")
async def drip(bytes_per_s: int = 10, total: int = 10_000) -> StreamingResponse:
    """Headers immediately, then the body trickles."""

    async def gen() -> AsyncIterator[bytes]:
        for _ in range(total // max(bytes_per_s, 1)):
            yield b"." * bytes_per_s
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/plain")


@app.get("/flaky")
async def flaky(fail_rate: float = 0.3, seed: int = 0) -> Response:
    """Deterministic pseudo-random 503s: request n for a seed always behaves the same."""
    n = _flaky_counters.get(seed, 0)
    _flaky_counters[seed] = n + 1
    if random.Random(f"{seed}:{n}").random() < fail_rate:
        return Response(content="flaky failure", status_code=503)
    return Response(content="ok", status_code=200)


class ScenarioIn(BaseModel):
    mode: str
    duration_s: int | None = None


_MODES = {"healthy", "slow_db", "outage", "broken_contract", "hang"}
_revert_task: asyncio.Task[None] | None = None


@app.get("/scenario")
async def get_scenario() -> dict[str, Any]:
    return _scenario


@app.post("/scenario")
async def set_scenario(body: ScenarioIn) -> Response:
    global _revert_task
    if body.mode not in _MODES:
        return JSONResponse({"error": f"mode must be one of {sorted(_MODES)}"}, status_code=422)
    _scenario["mode"] = body.mode
    if _revert_task:
        _revert_task.cancel()
    if body.duration_s:

        async def revert() -> None:
            await asyncio.sleep(body.duration_s or 0)
            _scenario["mode"] = "healthy"

        _revert_task = asyncio.create_task(revert())
    return JSONResponse(_scenario)


async def _scenario_delay(endpoint: str) -> None:
    mode = _scenario["mode"]
    if mode == "slow_db" and endpoint == "orders":
        await asyncio.sleep(random.Random(1).uniform(1.5, 3.0))
    elif mode == "hang" and endpoint == "orders":
        await asyncio.sleep(3600)
    else:
        await asyncio.sleep(0.02)


@app.get("/health")
async def health() -> dict[str, str]:
    await asyncio.sleep(0.02)
    return {"status": "ok"}


@app.get("/api/products")
async def products() -> Response:
    await _scenario_delay("products")
    mode = _scenario["mode"]
    if mode == "outage":
        return Response(content="outage", status_code=503)
    if mode == "broken_contract":
        return Response(content=json.dumps({"items": None}), media_type="application/json")
    return JSONResponse({"items": [{"id": 1, "name": "widget"}]})


@app.get("/api/orders")
async def orders() -> Response:
    await _scenario_delay("orders")
    if _scenario["mode"] == "outage":
        return Response(content="outage", status_code=503)
    return JSONResponse({"orders": []})
