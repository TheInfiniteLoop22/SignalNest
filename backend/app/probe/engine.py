"""Probe engine: ProbeRequest -> ProbeResult. Does no database I/O.

Resolve the host, check every address against the SSRF policy, pin the connection to one
validated IP, time each phase via httpcore trace hooks, then judge the response. The default
policy is strict; tests and dev opt in to private ranges explicitly.
"""

import asyncio
import functools
import ipaddress
import socket
import ssl
import time

import httpx

from app.probe.classify import classify_exception
from app.probe.ssrf import BlockedTarget, SsrfPolicy, TargetError
from app.probe.types import (
    DnsNameNotFound,
    DnsTimeout,
    FailureClass,
    Outcome,
    ProbeRequest,
    ProbeResult,
    Resolver,
)

BODY_CAP = 64 * 1024  # only the start of the body is read; body_contains looks inside it


class SystemResolver:
    """Resolves with the OS resolver (getaddrinfo) in a thread."""

    async def resolve(self, host: str, timeout_s: float) -> list[str]:
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout(timeout_s):
                infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except TimeoutError as exc:
            raise DnsTimeout(host) from exc
        except socket.gaierror as exc:
            if exc.errno == socket.EAI_AGAIN:
                raise DnsTimeout(host) from exc
            raise DnsNameNotFound(host) from exc
        ips = list(dict.fromkeys(str(info[4][0]) for info in infos))
        if not ips:
            raise DnsNameNotFound(host)
        return ips


class _Timings:
    """httpcore calls this for each phase event; we keep the timestamps."""

    def __init__(self) -> None:
        self._at: dict[str, float] = {}

    async def __call__(self, name: str, info: object) -> None:
        self._at[name] = time.monotonic()

    def span(self, start: str, end: str) -> int | None:
        if start in self._at and end in self._at:
            return round((self._at[end] - self._at[start]) * 1000)
        return None


@functools.lru_cache(maxsize=1)
def _default_ssl_context() -> ssl.SSLContext:
    """One verifying context for all probes: building one loads the CA bundle, which is CPU
    work on the event loop. Contexts are reusable."""
    return ssl.create_default_context()


def _pick_ip(ips: list[str]) -> str:
    """Prefer IPv4, which is the likelier to be reachable from a typical host."""
    for ip in ips:
        if ipaddress.ip_address(ip).version == 4:
            return ip
    return ips[0]


def _judge(req: ProbeRequest, status: int, body: bytes) -> tuple[FailureClass | None, str | None]:
    """Is this response acceptable? Returns the failure class and detail, or (None, None)."""
    if req.expected_status is not None:
        if status != req.expected_status:
            return FailureClass.BAD_RESPONSE, f"expected status {req.expected_status}, got {status}"
    elif not 200 <= status <= 399:
        return FailureClass.BAD_RESPONSE, f"unexpected status {status}"
    if req.body_contains is not None:
        if req.body_contains not in body.decode("utf-8", errors="replace"):
            return FailureClass.BAD_RESPONSE, "response body does not contain the expected text"
    return None, None


async def run_probe(
    req: ProbeRequest,
    *,
    resolver: Resolver | None = None,
    policy: SsrfPolicy | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> ProbeResult:
    resolver = resolver or SystemResolver()
    policy = policy or SsrfPolicy()  # strict by default
    t0 = time.monotonic()
    timings = _Timings()
    budget = req.timeout_ms / 1000
    resolved_ip: str | None = None
    dns_ms: int | None = None
    status: int | None = None
    failure: FailureClass | None = None
    detail: str | None = None

    try:
        target = policy.validate_url(req.url)  # raises TargetError / BlockedTarget
        host, parts = target.host, target.parts

        async with asyncio.timeout(budget):
            # 1. Resolve (an IP literal needs no lookup).
            if target.is_ip_literal:
                ips = [host]
            else:
                t_dns = time.monotonic()
                ips = await resolver.resolve(host, budget)
                dns_ms = round((time.monotonic() - t_dns) * 1000)

            # 2. Policy: reject if ANY resolved address is denied. This runs on every probe,
            #    which is what defeats DNS rebinding between create time and probe time.
            if not all(policy.allows_ip(ip) for ip in ips):
                raise BlockedTarget(host)

            # 3. Pin the connection to one validated IP, so the address we checked is the
            #    address we connect to. Host header and SNI keep the hostname semantics.
            resolved_ip = _pick_ip(ips)
            ip_literal = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
            port = f":{parts.port}" if parts.port else ""
            pinned = parts._replace(netloc=f"{ip_literal}{port}").geturl()
            host_header = f"[{host}]" if ":" in host else host

            async with httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(budget),
                limits=httpx.Limits(max_keepalive_connections=0),
                follow_redirects=False,
                trust_env=False,
                verify=ssl_context or _default_ssl_context(),
            ) as client:
                async with client.stream(
                    "GET",
                    pinned,
                    headers={"Host": f"{host_header}{port}"},
                    extensions={"trace": timings, "sni_hostname": host},
                ) as resp:
                    status = resp.status_code
                    body = b""
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) >= BODY_CAP:
                            break
            failure, detail = _judge(req, status, body)
    except TargetError as exc:
        failure, detail = FailureClass.BLOCKED, str(exc)[:200]
    except BlockedTarget as exc:
        failure, detail = FailureClass.BLOCKED, f"denied by policy: {exc}"[:200]
    except Exception as exc:  # classify decides; unknown -> ERROR
        failure = classify_exception(exc)
        detail = f"{type(exc).__name__}: {exc}"[:500]
        if resolved_ip:
            detail = detail.replace(resolved_ip, "<ip>")

    return ProbeResult(
        outcome=Outcome.PASS if failure is None else Outcome.FAIL,
        failure_class=failure,
        http_status=status,
        resolved_ip=resolved_ip,
        dns_ms=dns_ms,
        connect_ms=timings.span(
            "connection.connect_tcp.started", "connection.connect_tcp.complete"
        ),
        tls_ms=timings.span("connection.start_tls.started", "connection.start_tls.complete"),
        ttfb_ms=timings.span(
            "http11.receive_response_headers.started", "http11.receive_response_headers.complete"
        ),
        total_ms=round((time.monotonic() - t0) * 1000),
        detail=detail,
    )
