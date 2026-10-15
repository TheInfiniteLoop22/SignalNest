"""Map an exception raised during a probe to a FailureClass. Pure function.

Unknown exceptions map to ERROR, never to a more specific class that would blame the target
for our own bug.
"""

import socket
import ssl
from collections.abc import Iterator

import httpx

from app.probe.types import DnsNameNotFound, DnsTimeout, FailureClass


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


def classify_exception(exc: BaseException) -> FailureClass:
    chain = list(_chain(exc))

    def has(*types: type[BaseException]) -> bool:
        return any(isinstance(e, types) for e in chain)

    if has(DnsNameNotFound, socket.gaierror):
        return FailureClass.DNS
    if has(DnsTimeout, TimeoutError, httpx.TimeoutException):
        return FailureClass.TIMEOUT
    # Certificate verification errors are a subclass of SSLError, so one check covers both.
    if has(ssl.SSLError):
        return FailureClass.TLS
    if has(ConnectionError, httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError):
        return FailureClass.CONNECT
    return FailureClass.ERROR
