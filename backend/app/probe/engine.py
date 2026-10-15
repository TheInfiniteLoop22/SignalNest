"""Probe engine: ProbeRequest -> ProbeResult. Does no database I/O.

Resolve, validate every address against the SSRF policy, pin the connection to one
validated IP, phase timings via httpcore trace hooks, failure classification, assertions,
one retry. The default policy is strict; tests/dev opt in to private ranges explicitly.
"""

import asyncio
import functools
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.probe import assertions as asserts
from app.probe.classify import classify_exception
from app.probe.ssrf import BlockedTarget, SsrfPolicy, TargetError
from app.probe.types import (
    INCONCLUSIVE_CLASSES,
    AttemptResult,
    DnsNameNotFound,
    DnsTimeout,
    FailureClass,
    Outcome,
    Phase,
    ProbeRequest,
    ProbeResult,
    Resolver,
    StatusIn,
)

BODY_CAP = 64 * 1024
SNIPPET_BYTES = 2048
ALLOWED_RESPONSE_HEADERS = frozenset(
    {
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
)


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
        ips: list[str] = []
        for info in infos:
            ip = str(info[4][0])
            if ip not in ips:
                ips.append(ip)
        if not ips:
            raise DnsNameNotFound(host)
        return ips


@dataclass
class _Timings:
    """Collects httpcore trace events; also tracks the current phase."""

    phase: Phase = Phase.CONNECT
    _t: dict[str, float] | None = None

    def __post_init__(self) -> None:
        self._t = {}

    async def __call__(self, name: str, info: object) -> None:
        assert self._t is not None
        self._t[name] = time.monotonic()
        if name == "connection.start_tls.started":
            self.phase = Phase.TLS
        elif name == "connection.start_tls.complete" or name == "connection.connect_tcp.complete":
            self.phase = Phase.HEADERS
        elif name == "http11.receive_response_headers.complete":
            self.phase = Phase.BODY

    def span(self, start: str, end: str) -> int | None:
        assert self._t is not None
        if start in self._t and end in self._t:
            return round((self._t[end] - self._t[start]) * 1000)
        return None


@functools.lru_cache(maxsize=1)
def _default_ssl_context() -> ssl.SSLContext:
    """One verifying context for all probes. Building one loads the CA bundle (tens of ms of
    CPU on the event loop); doing that per probe throttles a worker. Contexts are reusable."""
    return ssl.create_default_context()


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _pick_ip(ips: list[str]) -> str:
    for ip in ips:
        if ipaddress.ip_address(ip).version == 4:
            return ip
    return ips[0]


@dataclass
class _Body:
    status: int | None = None
    headers: dict[str, str] | None = None
    data: bytes = b""
    truncated: bool = False


async def _attempt(
    req: ProbeRequest,
    *,
    resolver: Resolver,
    policy: SsrfPolicy,
    transport: httpx.AsyncBaseTransport | None,
    ssl_context: ssl.SSLContext | None,
) -> tuple[AttemptResult, _Body]:
    started_at = datetime.now(UTC)
    t0 = time.monotonic()
    timings = _Timings()
    body = _Body()
    resolved_ip: str | None = None
    dns_ms: int | None = None
    error_class: FailureClass | None = None
    detail: str | None = None
    budget = req.timeout_ms / 1000

    def elapsed_ms() -> int:
        return round((time.monotonic() - t0) * 1000)

    try:
        target = policy.validate_url(req.url)  # raises TargetError / BlockedTarget
        host, parts = target.host, target.parts

        async with asyncio.timeout(budget):
            # 1. resolve
            if target.is_ip_literal:
                ips = [host]
            else:
                timings.phase = Phase.DNS
                t_dns = time.monotonic()
                ips = await resolver.resolve(host, budget)
                dns_ms = round((time.monotonic() - t_dns) * 1000)
                timings.phase = Phase.CONNECT

            # 2. policy: reject if ANY resolved address is denied (re-checked every attempt,
            #    which is what defeats DNS rebinding between create time and probe time)
            if not all(policy.allows_ip(ip) for ip in ips):
                raise BlockedTarget(host)

            # 3. pin to one validated IP; Host header + SNI keep the hostname semantics
            resolved_ip = _pick_ip(ips)
            ip_literal = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
            port = f":{parts.port}" if parts.port else ""
            pinned = parts._replace(netloc=f"{ip_literal}{port}").geturl()
            headers = {k: v for k, v in req.headers.items() if k.lower() != "host"}
            host_header = f"[{host}]" if ":" in host else host
            headers["Host"] = f"{host_header}{port}"

            limits = httpx.Limits(max_keepalive_connections=0)
            tmo = httpx.Timeout(budget)
            async with httpx.AsyncClient(
                transport=transport,
                timeout=tmo,
                limits=limits,
                follow_redirects=False,
                trust_env=False,
                verify=ssl_context or _default_ssl_context(),
            ) as client:
                async with client.stream(
                    req.method,
                    pinned,
                    headers=headers,
                    extensions={"trace": timings, "sni_hostname": host},
                ) as resp:
                    body.status = resp.status_code
                    body.headers = {
                        k.lower(): v
                        for k, v in resp.headers.items()
                        if k.lower() in ALLOWED_RESPONSE_HEADERS
                    }
                    timings.phase = Phase.BODY
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in resp.aiter_bytes():
                        room = BODY_CAP - size
                        if len(chunk) > room:
                            chunks.append(chunk[:room])
                            body.truncated = True
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    body.data = b"".join(chunks)
    except TargetError as exc:
        error_class, detail = FailureClass.INVALID_TARGET, str(exc)[:200]
    except BlockedTarget as exc:
        error_class, detail = FailureClass.BLOCKED_BY_POLICY, f"denied by policy: {exc}"[:200]
    except Exception as exc:  # classify decides; unknown -> PROBE_INTERNAL_ERROR
        error_class = classify_exception(exc, timings.phase)
        detail = f"{type(exc).__name__}: {exc}"[:500]
        if resolved_ip:
            detail = detail.replace(resolved_ip, "<ip>")

    result = AttemptResult(
        started_at=started_at,
        resolved_ip=resolved_ip,
        dns_ms=dns_ms,
        connect_ms=timings.span(
            "connection.connect_tcp.started", "connection.connect_tcp.complete"
        ),
        tls_ms=timings.span("connection.start_tls.started", "connection.start_tls.complete"),
        ttfb_ms=timings.span(
            "http11.receive_response_headers.started", "http11.receive_response_headers.complete"
        ),
        total_ms=elapsed_ms(),
        http_status=body.status,
        error_class=error_class,
        error_detail=detail,
    )
    return result, body


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
    attempt, body = await _attempt(
        req, resolver=resolver, policy=policy, transport=transport, ssl_context=ssl_context
    )
    attempts = [attempt]

    final = attempts[-1]
    snippet = body.data[:SNIPPET_BYTES].decode("utf-8", errors="replace") if body.status else None

    # transport / protocol level failure
    if final.error_class is not None:
        outcome = (
            Outcome.INCONCLUSIVE if final.error_class in INCONCLUSIVE_CLASSES else Outcome.FAIL
        )
        return ProbeResult(
            outcome,
            final.error_class,
            attempts,
            [],
            body.headers,
            snippet,
            body.truncated,
            final.error_detail,
        )

    results = asserts.evaluate(
        req.assertions, http_status=final.http_status, total_ms=final.total_ms, body=body.data
    )
    has_status_assertion = any(isinstance(a, StatusIn) for a in req.assertions)
    status_ok = (
        all(r.passed for r in results if r.type == "status_in")
        if has_status_assertion
        else asserts.default_status_allowed(final.http_status)
    )

    failure: FailureClass | None = None
    detail: str | None = None
    if not status_ok:
        failure = FailureClass.HTTP_UNEXPECTED_STATUS
        kind = "5xx" if (final.http_status or 0) >= 500 else "4xx/other"
        detail = f"status {final.http_status} ({kind})"
    elif any(not r.passed for r in results):
        failure = FailureClass.ASSERTION_FAILED
        detail = "; ".join(f"{r.type}: {r.message}" for r in results if not r.passed)[:500]

    if failure is not None:
        outcome = Outcome.FAIL
    elif req.degraded_ms is not None and final.total_ms > req.degraded_ms:
        outcome = Outcome.DEGRADED
    else:
        outcome = Outcome.PASS

    return ProbeResult(
        outcome, failure, attempts, results, body.headers, snippet, body.truncated, detail
    )
