import pytest

from app.api.schemas import MonitorCreate
from app.security import (
    hash_password,
    needs_rehash,
    new_session_token,
    token_hash,
    verify_password,
)


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


def test_monitor_schema_defaults_and_bounds() -> None:
    m = MonitorCreate(name="x", url="https://a.example.com")
    assert m.failure_threshold == 3 and m.expected_status is None
    for bad in ({"timeout_ms": 10}, {"interval_seconds": 0}, {"expected_status": 99}):
        with pytest.raises(ValueError):
            MonitorCreate(name="x", url="https://a.example.com", **bad)


@pytest.mark.parametrize(
    "given",
    ["postgres://u:p@h:5432/d", "postgresql://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"],
)
def test_database_url_always_uses_the_async_driver(given: str) -> None:
    from app.config import Settings

    assert Settings(database_url=given).database_url == "postgresql+asyncpg://u:p@h:5432/d"


def test_hashes_made_with_weaker_settings_are_flagged_for_upgrade() -> None:
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("a-long-enough-password")
    assert needs_rehash(weak)
    assert not needs_rehash(hash_password("a-long-enough-password"))
