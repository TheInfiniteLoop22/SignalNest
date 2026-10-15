"""Map exceptions raised during an attempt to a FailureClass.

Pure function. Unknown exceptions map to PROBE_INTERNAL_ERROR, never silently to a
target failure (that would blame the target for our bug).
"""

import socket
import ssl
from collections.abc import Iterator

import httpx

from app.probe.types import DnsNameNotFound, DnsTimeout, FailureClass, Phase


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield exc and everything it was raised from / during, without looping."""
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))
        yield cur
        stack.extend([cur.__cause__, cur.__context__])


def classify_exception(exc: BaseException, phase: Phase) -> FailureClass:
    chain = list(_chain(exc))

    if any(isinstance(e, DnsNameNotFound) for e in chain):
        return FailureClass.DNS_FAILURE
    if any(isinstance(e, DnsTimeout) for e in chain):
        return FailureClass.DNS_TIMEOUT
    if any(isinstance(e, socket.gaierror) for e in chain):
        return FailureClass.DNS_FAILURE

    # TLS: cert verification is a subclass of SSLError, so test it first.
    if any(isinstance(e, ssl.SSLCertVerificationError) for e in chain):
        return FailureClass.TLS_CERT_INVALID
    if any(isinstance(e, ssl.SSLError) for e in chain):
        return FailureClass.TLS_ERROR

    if any(isinstance(e, ConnectionRefusedError) for e in chain):
        return FailureClass.CONNECTION_REFUSED

    # Timeouts: httpx's own types say which phase stalled; a bare TimeoutError comes
    # from our overall deadline, so the tracked phase decides.
    if isinstance(exc, httpx.ConnectTimeout):
        return FailureClass.CONNECT_TIMEOUT
    if isinstance(exc, httpx.ReadTimeout | httpx.WriteTimeout):
        return FailureClass.TOTAL_TIMEOUT if phase is Phase.BODY else FailureClass.READ_TIMEOUT
    if isinstance(exc, TimeoutError):
        if phase in (Phase.DNS,):
            return FailureClass.DNS_TIMEOUT
        if phase in (Phase.CONNECT, Phase.TLS):
            return FailureClass.CONNECT_TIMEOUT
        if phase is Phase.HEADERS:
            return FailureClass.READ_TIMEOUT
        return FailureClass.TOTAL_TIMEOUT

    # Peer closed or reset after connecting.
    if any(
        isinstance(e, ConnectionResetError | BrokenPipeError | ConnectionAbortedError)
        for e in chain
    ):
        return FailureClass.CONNECTION_RESET
    if isinstance(exc, httpx.RemoteProtocolError | httpx.ReadError):
        return FailureClass.CONNECTION_RESET

    return FailureClass.PROBE_INTERNAL_ERROR
