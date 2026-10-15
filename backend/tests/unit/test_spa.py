from pathlib import Path

import httpx
import pytest

from app.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def spa_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    (tmp_path / "index.html").write_text("<html>spa</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("top secret")
    monkeypatch.setattr("app.main.get_settings", lambda: Settings(static_dir=str(tmp_path)))
    return create_app()


async def get(app, path: str) -> httpx.Response:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


async def test_serves_index_for_client_routes_and_assets(spa_app) -> None:  # type: ignore[no-untyped-def]
    assert (await get(spa_app, "/")).text == "<html>spa</html>"
    assert (await get(spa_app, "/monitors/123")).text == "<html>spa</html>"
    assert (await get(spa_app, "/assets/app.js")).text == "console.log(1)"


async def test_unknown_api_paths_are_not_swallowed_by_the_spa(spa_app) -> None:  # type: ignore[no-untyped-def]
    r = await get(spa_app, "/api/v1/nope")
    assert r.status_code == 404
    assert "spa" not in r.text


async def test_path_traversal_cannot_escape_static_root(spa_app) -> None:  # type: ignore[no-untyped-def]
    for p in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        r = await get(spa_app, p)
        assert "top secret" not in r.text, p


async def test_security_headers_on_every_response(spa_app) -> None:  # type: ignore[no-untyped-def]
    r = await get(spa_app, "/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_static_serving_is_off_by_default() -> None:
    assert get_settings().static_dir == ""
