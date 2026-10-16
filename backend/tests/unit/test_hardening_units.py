from app.api.csrf import origin_allowed
from app.worker.alerts import backoff_s

TRUSTED = frozenset({"https://app.example.com"})


def test_no_origin_header_is_allowed() -> None:
    assert origin_allowed(None, "signalnest.test", frozenset())


def test_same_origin_is_allowed() -> None:
    assert origin_allowed("https://signalnest.test", "signalnest.test", frozenset())
    assert origin_allowed("http://localhost:5173", "localhost:5173", frozenset())


def test_other_origins_are_refused() -> None:
    assert not origin_allowed("https://evil.test", "signalnest.test", frozenset())
    assert not origin_allowed("https://signalnest.test.evil.test", "signalnest.test", frozenset())
    assert not origin_allowed("null", "signalnest.test", frozenset())
    assert not origin_allowed("https://signalnest.test", None, frozenset())


def test_trusted_origins_are_allowed() -> None:
    assert origin_allowed("https://app.example.com", "signalnest.test", TRUSTED)


def test_backoff_doubles_and_is_capped() -> None:
    assert [backoff_s(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert backoff_s(20) == 3600
