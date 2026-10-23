import httpx
import pytest
from fastapi import FastAPI

from app.api.csrf import OriginCheckMiddleware, origin_allowed


@pytest.mark.parametrize(
    ("origin", "host", "allowed"),
    [
        (None, "app.test", True),  # curl and other non-browser clients send no Origin
        ("https://app.test", "app.test", True),  # same origin
        ("http://localhost:5173", "localhost:5173", True),  # dev server
        ("https://evil.example", "app.test", False),
        ("https://app.test.evil.example", "app.test", False),
        ("https://app.test:8443", "app.test", False),  # different port = different origin
        ("null", "app.test", False),  # sandboxed pages send the string "null"
        ("ftp://app.test", "app.test", False),
    ],
)
def test_origin_allowed(origin: str | None, host: str, allowed: bool) -> None:
    assert origin_allowed(origin, host, frozenset()) is allowed


def test_trusted_origins_are_allowed() -> None:
    assert origin_allowed("https://ui.example", "api.test", frozenset({"https://ui.example"}))


async def test_middleware_blocks_unsafe_methods_from_foreign_origins_only() -> None:
    app = FastAPI()
    app.add_middleware(OriginCheckMiddleware, trusted_origins="https://ui.example/")

    @app.post("/api/x")
    async def post_x() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/x")
    async def get_x() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/other")
    async def post_other() -> dict[str, bool]:
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        evil = {"Origin": "https://evil.example"}
        refused = await c.post("/api/x", headers=evil)
        assert refused.status_code == 403
        assert refused.json() == {"detail": "cross-origin request refused"}
        assert (await c.post("/api/x", headers={"Origin": "http://t"})).status_code == 200
        assert (await c.post("/api/x")).status_code == 200  # no Origin
        assert (await c.post("/api/x", headers={"Origin": "https://ui.example"})).status_code == 200
        assert (await c.get("/api/x", headers=evil)).status_code == 200  # reads are not guarded
        assert (await c.post("/other", headers=evil)).status_code == 200  # only /api/ is guarded
