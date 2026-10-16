"""Value types for the probe engine. No I/O, no database."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class FailureClass(StrEnum):
    """Why a probe failed, coarse enough to read at a glance. `detail` carries the specifics."""

    DNS = "DNS"  # name does not resolve, or the resolver is unreachable
    CONNECT = "CONNECT"  # refused, reset, or the connection never opened
    TLS = "TLS"  # handshake or certificate verification failed
    TIMEOUT = "TIMEOUT"  # the whole probe exceeded its time budget
    BAD_RESPONSE = "BAD_RESPONSE"  # got an answer, but the status or body was not what we expect
    BLOCKED = "BLOCKED"  # SSRF policy refused the target (or the URL is not usable)
    ERROR = "ERROR"  # anything else, including bugs in the probe itself


@dataclass(frozen=True)
class ProbeRequest:
    url: str
    timeout_ms: int = 5000
    expected_status: int | None = None  # None: any 2xx or 3xx
    body_contains: str | None = None
    method: str = "GET"
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes | None = None  # request body, e.g. a webhook payload


@dataclass(frozen=True)
class ProbeResult:
    outcome: Outcome
    failure_class: FailureClass | None
    http_status: int | None
    resolved_ip: str | None
    dns_ms: int | None
    connect_ms: int | None
    tls_ms: int | None
    ttfb_ms: int | None
    total_ms: int
    detail: str | None = None


# --- seams (injected so tests are deterministic) -------------------------------------


class DnsNameNotFound(Exception):
    """NXDOMAIN / no address records."""


class DnsTimeout(Exception):
    """Resolver did not answer in time (temporary failure)."""


class Resolver(Protocol):
    async def resolve(self, host: str, timeout_s: float) -> list[str]: ...
