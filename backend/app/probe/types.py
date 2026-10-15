"""Value types for the probe engine. No I/O, no database."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class Outcome(StrEnum):
    PASS = "PASS"
    DEGRADED = "DEGRADED"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class FailureClass(StrEnum):
    INVALID_TARGET = "INVALID_TARGET"
    DNS_FAILURE = "DNS_FAILURE"
    DNS_TIMEOUT = "DNS_TIMEOUT"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    CONNECTION_REFUSED = "CONNECTION_REFUSED"
    CONNECT_TIMEOUT = "CONNECT_TIMEOUT"
    CONNECTION_RESET = "CONNECTION_RESET"
    TLS_CERT_INVALID = "TLS_CERT_INVALID"
    TLS_ERROR = "TLS_ERROR"
    READ_TIMEOUT = "READ_TIMEOUT"
    TOTAL_TIMEOUT = "TOTAL_TIMEOUT"
    HTTP_UNEXPECTED_STATUS = "HTTP_UNEXPECTED_STATUS"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    PROBE_INTERNAL_ERROR = "PROBE_INTERNAL_ERROR"


# Classes caused by our side (or by policy), never counted against the target.
INCONCLUSIVE_CLASSES = frozenset(
    {FailureClass.BLOCKED_BY_POLICY, FailureClass.PROBE_INTERNAL_ERROR}
)
# The only classes worth one retry (07 §5).
RETRYABLE_CLASSES = frozenset(
    {FailureClass.DNS_TIMEOUT, FailureClass.CONNECT_TIMEOUT, FailureClass.CONNECTION_RESET}
)


class Phase(StrEnum):
    """Where an attempt was when it failed; disambiguates timeouts."""

    DNS = "dns"
    CONNECT = "connect"
    TLS = "tls"
    HEADERS = "headers"
    BODY = "body"


# --- assertions ------------------------------------------------------------------------


class StatusIn(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    type: Literal["status_in"] = "status_in"
    values: list[int] | None = None
    range_: tuple[int, int] | None = Field(default=None, alias="range")

    @model_validator(mode="after")
    def _exactly_one(self) -> "StatusIn":
        if (self.values is None) == (self.range_ is None):
            raise ValueError("provide exactly one of 'values' or 'range'")
        return self


class LatencyBelow(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["latency_below"] = "latency_below"
    ms: int = Field(gt=0)


class BodyContains(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["body_contains"] = "body_contains"
    text: str = Field(min_length=1, max_length=200)


class JsonEquals(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["json_equals"] = "json_equals"
    path: str = Field(min_length=1)
    value: str | int | float | bool | None


Assertion = Annotated[
    StatusIn | LatencyBelow | BodyContains | JsonEquals, Field(discriminator="type")
]
assertion_list_adapter: TypeAdapter[list[Assertion]] = TypeAdapter(list[Assertion])


@dataclass(frozen=True)
class AssertionResult:
    type: str
    expected: Any
    actual: Any
    passed: bool
    message: str = ""


# --- request / result ------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeRequest:
    url: str
    method: Literal["GET", "HEAD"] = "GET"
    timeout_ms: int = 5000
    headers: Mapping[str, str] = field(default_factory=dict)
    assertions: Sequence[Assertion] = ()
    degraded_ms: int | None = None


@dataclass(frozen=True)
class AttemptResult:
    started_at: datetime
    resolved_ip: str | None
    dns_ms: int | None
    connect_ms: int | None
    tls_ms: int | None
    ttfb_ms: int | None
    total_ms: int
    http_status: int | None
    error_class: FailureClass | None
    error_detail: str | None


@dataclass(frozen=True)
class ProbeResult:
    outcome: Outcome
    failure_class: FailureClass | None
    attempts: list[AttemptResult]  # 1 or 2; the last one is the "final" attempt
    assertion_results: list[AssertionResult]
    response_headers: dict[str, str] | None
    response_snippet: str | None
    body_truncated: bool = False
    error_detail: str | None = None

    @property
    def final(self) -> AttemptResult:
        return self.attempts[-1]


# --- seams (injected so tests are deterministic) -------------------------------------


class DnsNameNotFound(Exception):
    """NXDOMAIN / no address records."""


class DnsTimeout(Exception):
    """Resolver did not answer in time (temporary failure)."""


class Resolver(Protocol):
    async def resolve(self, host: str, timeout_s: float) -> list[str]: ...


# A policy returns True when connecting to the IP is allowed. M3 replaces the default.
TargetPolicy = Callable[[str], bool]
Sleeper = Callable[[float], Awaitable[None]]
