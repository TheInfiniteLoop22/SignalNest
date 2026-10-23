"""SSRF guard tests: IP pinning, TLS verification by hostname, DNS rebinding."""

import ipaddress
import ssl

import httpx
import trustme
from faultline.socket_faults import start_tls_server

from app.probe.engine import run_probe
from app.probe.ssrf import SsrfPolicy
from app.probe.types import FailureClass, Outcome, ProbeRequest

LOOPBACK_OK = SsrfPolicy(
    allow_private_cidrs=(ipaddress.ip_network("127.0.0.0/8"),), allowed_ports=None
)


class Fixed:
    def __init__(self, *answers: list[str]) -> None:
        self.answers, self.calls = list(answers), 0

    async def resolve(self, host: str, timeout_s: float) -> list[str]:
        ans = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return ans


async def _tls_probe(cert_name: str) -> tuple[Outcome, FailureClass | None, int | None]:
    ca = trustme.CA()
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert(cert_name).configure_cert(server_ctx)
    client_ctx = ssl.create_default_context()
    ca.configure_trust(client_ctx)

    srv = await start_tls_server(server_ctx)
    try:
        port = srv.sockets[0].getsockname()[1]
        r = await run_probe(
            ProbeRequest(url=f"https://faultline.test:{port}/", timeout_ms=3000),
            resolver=Fixed(["127.0.0.1"]),
            policy=LOOPBACK_OK,
            ssl_context=client_ctx,
        )
    finally:
        srv.close()
    return r.outcome, r.failure_class, r.tls_ms


async def test_tls_is_verified_against_hostname_when_connecting_by_ip() -> None:
    outcome, failure, tls_ms = await _tls_probe("faultline.test")
    assert (outcome, failure) == (Outcome.PASS, None)
    assert tls_ms is not None


async def test_certificate_for_another_name_is_rejected() -> None:
    outcome, failure, _ = await _tls_probe("someone-else.test")
    assert outcome is Outcome.FAIL
    assert failure is FailureClass.TLS


async def test_request_goes_to_pinned_ip_with_original_host_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    r = await run_probe(
        ProbeRequest(url="http://api.example.com:8080/v1/ping?x=1", timeout_ms=2000),
        resolver=Fixed(["93.184.216.34"]),
        transport=httpx.MockTransport(handler),
    )
    assert r.outcome is Outcome.PASS
    req = seen[0]
    assert req.url.host == "93.184.216.34"
    assert req.url.port == 8080
    assert req.headers["host"] == "api.example.com:8080"
    assert req.url.path == "/v1/ping"
    assert req.url.query == b"x=1"
    assert req.extensions["sni_hostname"] == "api.example.com"


async def test_dns_rebinding_public_then_private_is_blocked() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    resolver = Fixed(["93.184.216.34"], ["10.0.0.5"])
    req = ProbeRequest(url="http://rebind.example.com/", timeout_ms=2000)
    transport = httpx.MockTransport(handler)

    first = await run_probe(req, resolver=resolver, transport=transport)
    assert first.outcome is Outcome.PASS

    second = await run_probe(req, resolver=resolver, transport=transport)
    assert second.outcome is Outcome.FAIL
    assert second.failure_class is FailureClass.BLOCKED
    assert len(sent) == 1  # nothing was sent for the second probe


async def test_mixed_public_and_private_answers_are_rejected() -> None:
    sent: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))
    r = await run_probe(
        ProbeRequest(url="http://mixed.example.com/", timeout_ms=2000),
        resolver=Fixed(["93.184.216.34", "169.254.169.254"]),
        transport=transport,
    )
    assert r.failure_class is FailureClass.BLOCKED
    assert sent == []


async def test_literal_loopback_url_is_blocked_by_default_policy() -> None:
    sent: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))
    for url in ("http://127.0.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/"):
        r = await run_probe(ProbeRequest(url=url, timeout_ms=1000), transport=transport)
        assert r.failure_class is FailureClass.BLOCKED, url
    assert sent == []


async def test_names_that_resolve_to_loopback_are_blocked() -> None:
    # "localhost" and the numeric form 2130706433 both resolve to 127.0.0.1.
    for url in ("http://localhost/", "http://2130706433/"):
        r = await run_probe(ProbeRequest(url=url, timeout_ms=1000), resolver=Fixed(["127.0.0.1"]))
        assert r.failure_class is FailureClass.BLOCKED, url
