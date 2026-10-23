"""Turn an exception raised during a probe into a FailureClass. A pure function.

Unknown exceptions become ERROR, never a more specific class that would blame the target for
our own bug.
"""

import socket
import ssl

import httpx

from app.probe.types import DnsNameNotFound, DnsTimeout, FailureClass


def _causes(exc: BaseException) -> list[BaseException]:
    """The exception plus everything it was raised from. httpx wraps low-level errors, so the
    real reason (say, a certificate error) is usually a few levels down."""
    found: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in found:
        found.append(current)
        current = current.__cause__ or current.__context__
    return found


def classify_exception(exc: BaseException) -> FailureClass:
    causes = _causes(exc)

    def has(*types: type[BaseException]) -> bool:
        return any(isinstance(e, types) for e in causes)

    if has(DnsNameNotFound, socket.gaierror):
        return FailureClass.DNS
    if has(DnsTimeout, TimeoutError, httpx.TimeoutException):
        return FailureClass.TIMEOUT
    if has(ssl.SSLError):  # certificate verification errors are SSLErrors too
        return FailureClass.TLS
    if has(ConnectionError, httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError):
        return FailureClass.CONNECT
    return FailureClass.ERROR
