from datetime import UTC, datetime

import pytest

from app.api.schemas import MonitorCreate
from app.security import hash_password, new_session_token, token_hash, verify_password
from app.services.cursor import BadCursor, decode_cursor, encode_cursor


def test_cursor_roundtrip() -> None:
    ts = datetime(2026, 10, 15, 10, 0, 0, 123456, tzinfo=UTC)
    assert decode_cursor(encode_cursor(ts, 42)) == (ts, 42)


@pytest.mark.parametrize("bad", ["", "!!!", "e30", "WzEsMl0", "WyIyMDI2LTAxLTAxIiwgMV0"])
def test_cursor_rejects_garbage(bad: str) -> None:
    with pytest.raises(BadCursor):
        decode_cursor(bad)


def test_cursor_rejects_naive_timestamp() -> None:
    import base64
    import json

    raw = base64.urlsafe_b64encode(json.dumps(["2026-01-01T00:00:00", 1]).encode()).decode()
    with pytest.raises(BadCursor):
        decode_cursor(raw)


def test_password_hash_is_argon2id_and_verifies() -> None:
    h = hash_password("a-long-enough-password")
    assert h.startswith("$argon2id$")
    assert verify_password("a-long-enough-password", h)
    assert not verify_password("another-password-xx", h)
    assert not verify_password("anything", None)  # unknown user path
    assert not verify_password("anything", "!")  # unusable seeded hash


def test_session_token_is_high_entropy_and_hashed() -> None:
    a, b = new_session_token(), new_session_token()
    assert a != b and len(a) >= 43
    assert token_hash(a) != token_hash(b)
    assert len(token_hash(a)) == 32


def test_monitor_schema_normalises_and_validates() -> None:
    m = MonitorCreate(name="x", url="https://a.example.com", headers={"Accept": "a"})
    assert m.method == "GET" and m.failure_threshold == 3
    with pytest.raises(ValueError):
        MonitorCreate(
            name="x", url="https://a.example.com", headers={f"h{i}": "v" for i in range(11)}
        )
    with pytest.raises(ValueError):
        MonitorCreate(name="x", url="https://a.example.com", headers={"X": "a\r\nInjected: 1"})
    with pytest.raises(ValueError):
        MonitorCreate(
            name="x",
            url="https://a.example.com",
            assertions=[{"type": "latency_below", "ms": 1}] * 9,
        )
