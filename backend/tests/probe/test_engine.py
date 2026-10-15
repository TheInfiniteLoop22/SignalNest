import asyncio
import ssl
from typing import Any

import httpx
import pytest
import trustme
from faultline.socket_faults import (
    free_port,
    start_close_server,
    start_rst_server,
    start_tls_server,
)

from app.probe.engine import run_probe
from app.probe.types import (
    DnsNameNotFound,
    DnsTimeout,
    FailureClass,
    JsonEquals,
    Outcome,
    ProbeRequest,
    StatusIn,
)

F = FailureClass
FAST: dict[str, Any] = {"retry_delay_s": 0.0}


def req(url: str, **kw: Any) -> ProbeRequest:
    kw.setdefault("timeout_ms", 2000)
    return ProbeRequest(url=url, **kw)


async def test_pass_has_phase_timings(faultline: str) -> None:
    r = await run_probe(req(f"{faultline}/ok"))
    assert r.outcome is Outcome.PASS
    a = r.final
    assert a.http_status == 200
    assert a.connect_ms is not None
    assert a.ttfb_ms is not None
    assert a.tls_ms is None  # plain HTTP
    assert a.resolved_ip == "127.0.0.1"


async def test_ac4_hang_is_read_timeout(faultline: str) -> None:
    r = await run_probe(req(f"{faultline}/hang", timeout_ms=1000))
    assert r.outcome is Outcome.FAIL
    assert r.failure_class is F.READ_TIMEOUT
    assert 1000 <= r.final.total_ms < 1400
    assert len(r.attempts) == 1  # READ_TIMEOUT is never retried


async def test_ac5_connection_refused_has_no_tls_or_ttfb() -> None:
    # Windows takes ~2 s to report a refused loopback connect (SYN retries), so allow 5 s.
    r = await run_probe(req(f"http://127.0.0.1:{free_port()}/", timeout_ms=5000), **FAST)
    assert r.failure_class is F.CONNECTION_REFUSED
    assert r.final.tls_ms is None
    assert r.final.ttfb_ms is None
    assert len(r.attempts) == 1


async def test_connection_reset_is_retried_once() -> None:
    srv = await start_rst_server()
    try:
        port = srv.sockets[0].getsockname()[1]
        r = await run_probe(req(f"http://127.0.0.1:{port}/"), **FAST)
    finally:
        srv.close()
    assert r.failure_class is F.CONNECTION_RESET
    assert len(r.attempts) == 2


async def test_clean_close_without_response_is_reset() -> None:
    srv = await start_close_server()
    try:
        port = srv.sockets[0].getsockname()[1]
        r = await run_probe(req(f"http://127.0.0.1:{port}/"), **FAST)
    finally:
        srv.close()
    assert r.failure_class is F.CONNECTION_RESET


async def test_untrusted_cert_is_tls_cert_invalid() -> None:
    ca = trustme.CA()
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert("localhost").configure_cert(ctx)
    srv = await start_tls_server(ctx)
    try:
        port = srv.sockets[0].getsockname()[1]
        # System trust store does not contain the private CA.
        r = await run_probe(req(f"https://127.0.0.1:{port}/"), **FAST)
    finally:
        srv.close()
    assert r.failure_class is F.TLS_CERT_INVALID
    assert r.outcome is Outcome.FAIL


async def test_drip_is_total_timeout(faultline: str) -> None:
    r = await run_probe(req(f"{faultline}/drip?bytes_per_s=1", timeout_ms=1500))
    assert r.failure_class is F.TOTAL_TIMEOUT
    assert r.final.http_status == 200  # headers arrived; the body never finished


async def test_status_503_is_unexpected_status(faultline: str) -> None:
    r = await run_probe(req(f"{faultline}/status/503"))
    assert r.failure_class is F.HTTP_UNEXPECTED_STATUS
    assert len(r.attempts) == 1


async def test_status_assertion_can_allow_404(faultline: str) -> None:
    r = await run_probe(req(f"{faultline}/status/404", assertions=[StatusIn(values=[404])]))
    assert r.outcome is Outcome.PASS


async def test_ac10_json_assertion_failure(faultline: str) -> None:
    r = await run_probe(
        req(f"{faultline}/json?status=down", assertions=[JsonEquals(path="status", value="up")])
    )
    assert r.outcome is Outcome.FAIL
    assert r.failure_class is F.ASSERTION_FAILED
    assert r.assertion_results[0].actual == "down"


async def test_redirect_is_not_followed(faultline: str) -> None:
    r = await run_probe(req(f"{faultline}/redirect?to=/ok"))
    assert r.final.http_status == 302
    assert r.outcome is Outcome.PASS  # 3xx counts as reachable (ADR-010)


async def test_big_body_is_truncated(faultline: str) -> None:
    r = await run_probe(req(f"{faultline}/big?kb=500"))
    assert r.outcome is Outcome.PASS
    assert r.body_truncated
    assert r.response_snippet is not None
    assert len(r.response_snippet) == 2048


async def test_slow_is_degraded(faultline: str) -> None:
    r = await run_probe(req(f"{faultline}/slow?ms=300", degraded_ms=100))
    assert r.outcome is Outcome.DEGRADED
    assert r.failure_class is None


async def test_response_headers_are_allow_listed(faultline: str) -> None:
    r = await run_probe(req(f"{faultline}/ok"))
    assert r.response_headers is not None
    assert "content-type" in r.response_headers
    allowed = {
        "content-type",
        "content-length",
        "server",
        "date",
        "cache-control",
        "retry-after",
        "location",
        "x-request-id",
        "via",
    }
    assert set(r.response_headers) <= allowed


class FakeResolver:
    def __init__(self, exc: Exception | None = None, ips: list[str] | None = None) -> None:
        self.exc, self.ips, self.calls = exc, ips or [], 0

    async def resolve(self, host: str, timeout_s: float) -> list[str]:
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.ips


async def test_dns_failure_is_not_retried() -> None:
    res = FakeResolver(DnsNameNotFound("nx"))
    r = await run_probe(req("http://nope.test/"), resolver=res, **FAST)
    assert r.failure_class is F.DNS_FAILURE
    assert res.calls == 1


async def test_dns_timeout_is_retried_once() -> None:
    res = FakeResolver(DnsTimeout("slow"))
    r = await run_probe(req("http://slow.test/"), resolver=res, **FAST)
    assert r.failure_class is F.DNS_TIMEOUT
    assert res.calls == 2


async def test_pass_on_retry_keeps_both_attempts(faultline: str) -> None:
    calls = {"n": 0}

    class FlakyResolver:
        async def resolve(self, host: str, timeout_s: float) -> list[str]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise DnsTimeout(host)
            return ["127.0.0.1"]

    port = faultline.rsplit(":", 1)[1]
    r = await run_probe(req(f"http://faultline.test:{port}/ok"), resolver=FlakyResolver(), **FAST)
    assert r.outcome is Outcome.PASS
    assert len(r.attempts) == 2
    assert r.attempts[0].error_class is F.DNS_TIMEOUT
    assert r.final.dns_ms is not None


async def test_policy_block_is_inconclusive_and_sends_nothing() -> None:
    res = FakeResolver(ips=["10.0.0.5"])
    r = await run_probe(req("http://internal.test/"), resolver=res, policy=lambda ip: False)
    assert r.outcome is Outcome.INCONCLUSIVE
    assert r.failure_class is F.BLOCKED_BY_POLICY
    assert r.final.http_status is None
    assert r.final.connect_ms is None


async def test_connect_timeout_via_transport_seam() -> None:
    class NeverConnects(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    r = await run_probe(
        req("http://127.0.0.1:9/", timeout_ms=1000), transport=NeverConnects(), **FAST
    )
    # No trace events fire through a custom transport, so the phase is still CONNECT.
    assert r.failure_class is F.CONNECT_TIMEOUT
    assert len(r.attempts) == 2


@pytest.mark.parametrize("url", ["ftp://x/", "http://user:pw@x/", "notaurl"])
async def test_invalid_target(url: str) -> None:
    r = await run_probe(req(url))
    assert r.failure_class is F.INVALID_TARGET
    assert r.outcome is Outcome.FAIL
