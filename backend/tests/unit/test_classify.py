import socket
import ssl

import httpx
import pytest

from app.probe.classify import classify_exception
from app.probe.types import DnsNameNotFound, DnsTimeout, FailureClass

F = FailureClass


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (DnsNameNotFound("x"), F.DNS),
        (socket.gaierror("nope"), F.DNS),
        (DnsTimeout("x"), F.TIMEOUT),
        (TimeoutError(), F.TIMEOUT),
        (httpx.ConnectTimeout("x"), F.TIMEOUT),
        (ssl.SSLCertVerificationError("bad cert"), F.TLS),
        (ssl.SSLError("handshake"), F.TLS),
        (ConnectionRefusedError(), F.CONNECT),
        (ConnectionResetError(), F.CONNECT),
        (httpx.RemoteProtocolError("closed"), F.CONNECT),
        (ValueError("a bug in our code"), F.ERROR),
    ],
)
def test_classification(exc: BaseException, expected: FailureClass) -> None:
    assert classify_exception(exc) is expected


def test_cause_chain_is_followed() -> None:
    try:
        try:
            raise ssl.SSLCertVerificationError("expired")
        except ssl.SSLError as inner:
            raise httpx.ConnectError("wrapped") from inner
    except httpx.ConnectError as outer:
        assert classify_exception(outer) is F.TLS
