import asyncio
from collections.abc import AsyncIterator

import pytest_asyncio
import uvicorn
from faultline.app import app


@pytest_asyncio.fixture
async def faultline() -> AsyncIterator[str]:
    """Faultline on an ephemeral localhost port; yields the base URL."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes no startup Event
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    server.force_exit = True
    try:
        await asyncio.wait_for(task, 3)
    except (TimeoutError, asyncio.CancelledError):
        pass  # abandoned connections (e.g. /hang) must not stall the suite
