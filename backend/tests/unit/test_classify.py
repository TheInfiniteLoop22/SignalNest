import socket
import ssl

import httpx
import pytest

from app.probe.classify import classify_exception
from app.probe.types import DnsNameNotFound, DnsTimeout, FailureClass, Phase

F = FailureClass


def chained(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


CASES = [
    (DnsNameNotFound("x"), Phase.DNS, F.DNS_FAILURE),
    (DnsTimeout("x"), Phase.DNS, F.DNS_TIMEOUT),
    (socket.gaierror(socket.EAI_NONAME, "no"), Phase.DNS, F.DNS_FAILURE),
    (
        chained(httpx.ConnectError("c"), ConnectionRefusedError()),
        Phase.CONNECT,
        F.CONNECTION_REFUSED,
    ),
    (httpx.ConnectTimeout("t"), Phase.CONNECT, F.CONNECT_TIMEOUT),
    (httpx.ReadTimeout("t"), Phase.HEADERS, F.READ_TIMEOUT),
    (httpx.ReadTimeout("t"), Phase.BODY, F.TOTAL_TIMEOUT),
    (TimeoutError(), Phase.CONNECT, F.CONNECT_TIMEOUT),
    (TimeoutError(), Phase.TLS, F.CONNECT_TIMEOUT),
    (TimeoutError(), Phase.HEADERS, F.READ_TIMEOUT),
    (TimeoutError(), Phase.BODY, F.TOTAL_TIMEOUT),
    (
        chained(httpx.ConnectError("c"), ssl.SSLCertVerificationError("bad")),
        Phase.TLS,
        F.TLS_CERT_INVALID,
    ),
    (chained(httpx.ConnectError("c"), ssl.SSLError("handshake")), Phase.TLS, F.TLS_ERROR),
    (chained(httpx.ReadError("r"), ConnectionResetError()), Phase.HEADERS, F.CONNECTION_RESET),
    (httpx.RemoteProtocolError("closed"), Phase.HEADERS, F.CONNECTION_RESET),
    (ValueError("our bug"), Phase.CONNECT, F.PROBE_INTERNAL_ERROR),
    (httpx.ConnectError("unknown cause"), Phase.CONNECT, F.PROBE_INTERNAL_ERROR),
]


@pytest.mark.parametrize(("exc", "phase", "expected"), CASES)
def test_classify(exc: BaseException, phase: Phase, expected: FailureClass) -> None:
    assert classify_exception(exc, phase) is expected


def test_cert_error_is_checked_before_generic_tls() -> None:
    # SSLCertVerificationError subclasses SSLError, so check order matters.
    assert issubclass(ssl.SSLCertVerificationError, ssl.SSLError)
