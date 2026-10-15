import ipaddress
import pathlib
import re

import pytest

from app.probe.ssrf import (
    BlockedTarget,
    SsrfPolicy,
    TargetError,
    is_ip_allowed,
    parse_cidrs,
    parse_ports,
)

# --- IP classification: (address, allowed?) -------------------------------------------

IP_CASES = [
    # public
    ("93.184.216.34", True),
    ("8.8.8.8", True),
    ("1.1.1.1", True),
    ("2606:2800:220:1:248:1893:25c8:1946", True),
    ("2001:4860:4860::8888", True),
    # this-host / loopback
    ("0.0.0.0", False),
    ("0.1.2.3", False),
    ("127.0.0.1", False),
    ("127.255.255.254", False),
    ("::", False),
    ("::1", False),
    # private
    ("10.0.0.1", False),
    ("172.16.0.1", False),
    ("172.31.255.255", False),
    ("172.32.0.1", True),  # just outside 172.16/12
    ("192.168.1.1", False),
    ("fc00::1", False),
    ("fd12:3456::1", False),
    # link-local and cloud metadata
    ("169.254.169.254", False),
    ("169.254.0.1", False),
    ("fe80::1", False),
    # CGNAT
    ("100.64.0.1", False),
    ("100.127.255.255", False),
    ("100.128.0.1", True),
    # special / test / benchmark
    ("192.0.0.1", False),
    ("192.0.2.1", False),
    ("198.18.0.1", False),
    ("198.19.255.255", False),
    ("198.51.100.1", False),
    ("203.0.113.1", False),
    ("2001:db8::1", False),
    # multicast / reserved / broadcast
    ("224.0.0.1", False),
    ("239.255.255.255", False),
    ("240.0.0.1", False),
    ("255.255.255.255", False),
    ("ff02::1", False),
    # IPv6 forms embedding IPv4
    ("::ffff:127.0.0.1", False),
    ("::ffff:10.0.0.1", False),
    ("::ffff:169.254.169.254", False),
    ("::ffff:93.184.216.34", True),
    ("64:ff9b::7f00:1", False),  # NAT64 -> 127.0.0.1
    ("64:ff9b::a00:1", False),  # NAT64 -> 10.0.0.1
    ("2002:7f00:1::", False),  # 6to4 -> 127.0.0.1
    ("2002:a9fe:a9fe::", False),  # 6to4 -> 169.254.169.254
    ("::7f00:1", False),  # deprecated IPv4-compatible
    ("2001:0:4136:e378:8000:63bf:3fff:fdd2", False),  # Teredo
    # garbage
    ("not-an-ip", False),
    ("", False),
]


@pytest.mark.parametrize(("ip", "allowed"), IP_CASES)
def test_is_ip_allowed(ip: str, allowed: bool) -> None:
    assert is_ip_allowed(ip) is allowed


def test_allow_cidr_override_for_dev() -> None:
    allow = parse_cidrs("172.16.0.0/12, 127.0.0.0/8")
    assert is_ip_allowed("172.18.0.5", allow)
    assert is_ip_allowed("127.0.0.1", allow)
    assert is_ip_allowed("::ffff:127.0.0.1", allow)  # unwrapped, then matched
    assert not is_ip_allowed("10.0.0.1", allow)  # not in the override
    assert not is_ip_allowed("169.254.169.254", allow)


def test_override_does_not_leak_across_ip_versions() -> None:
    allow = parse_cidrs("127.0.0.0/8")
    assert not is_ip_allowed("::1", allow)


def test_parse_helpers() -> None:
    assert parse_ports("80, 443,8000-8999") == [(80, 80), (443, 443), (8000, 8999)]
    assert parse_cidrs("") == []
    assert parse_cidrs("10.0.0.0/8") == [ipaddress.ip_network("10.0.0.0/8")]


# --- URL validation ---------------------------------------------------------------------

POLICY = SsrfPolicy()

OK_URLS = [
    ("http://example.com/", "example.com", 80),
    ("https://example.com/path?q=1", "example.com", 443),
    ("https://API.Example.COM:8443/x", "api.example.com", 8443),
    ("http://example.com.:8080/", "example.com", 8080),  # trailing dot stripped
    ("http://93.184.216.34/", "93.184.216.34", 80),
    ("http://[2606:2800:220:1::1]:8000/", "2606:2800:220:1::1", 8000),
    ("https://bücher.example/", "xn--bcher-kva.example", 443),  # IDNA
]


@pytest.mark.parametrize(("url", "host", "port"), OK_URLS)
def test_valid_urls(url: str, host: str, port: int) -> None:
    t = POLICY.validate_url(url)
    assert (t.host, t.port) == (host, port)


MALFORMED = [
    "ftp://example.com/",
    "file:///etc/passwd",
    "gopher://example.com/",
    "http://user:pw@example.com/",
    "http://user@example.com/",
    "http:///path",
    "example.com",
    "http://exa mple.com:notaport/",
]


@pytest.mark.parametrize("url", MALFORMED)
def test_malformed_urls(url: str) -> None:
    with pytest.raises(TargetError):
        POLICY.validate_url(url)


BLOCKED = [
    "http://localhost/",
    "http://LOCALHOST/",
    "http://localhost./",
    "http://app.localhost/",
    "http://printer.local/",
    "http://db.internal/",
    "http://intranet/",  # no dot
    "http://2130706433/",  # decimal 127.0.0.1
    "http://0x7f.1/",  # hex / short form
    "http://0x7f000001/",
    "http://017700000001/",  # octal
    "http://127.1/",  # short dotted form
    "http://example.com:22/",  # port not allowed
    "http://example.com:5432/",
    "http://example.com:65535/",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_blocked_urls(url: str) -> None:
    with pytest.raises(BlockedTarget):
        POLICY.validate_url(url)


def test_ip_literals_pass_static_check_but_fail_ip_check() -> None:
    # URL validation is static; address policy is applied to the literal by the engine.
    t = POLICY.validate_url("http://127.0.0.1/")
    assert t.is_ip_literal
    assert not POLICY.allows_ip(t.host)


def test_allowed_hosts_exempts_dotless_names_for_dev() -> None:
    policy = SsrfPolicy(allowed_hosts=frozenset({"faultline"}))
    assert policy.validate_url("http://faultline/ok").host == "faultline"
    with pytest.raises(BlockedTarget):
        policy.validate_url("http://other/ok")


def test_ports_none_disables_port_check() -> None:
    assert SsrfPolicy(allowed_ports=None).validate_url("http://example.com:22/").port == 22


def test_from_settings_logs_warning_when_override_set(caplog: pytest.LogCaptureFixture) -> None:
    SsrfPolicy.from_settings("80", "172.16.0.0/12")
    assert "SSRF_ALLOW_PRIVATE_CIDRS" in caplog.text


# --- architecture guard: outbound HTTP only inside app/probe --------------------------------


def test_httpx_is_only_imported_under_probe() -> None:
    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = [
        str(f.relative_to(app_dir))
        for f in app_dir.rglob("*.py")
        if "probe" not in f.relative_to(app_dir).parts
        and re.search(r"^\s*(import httpx|from httpx)", f.read_text(encoding="utf-8"), re.M)
    ]
    assert offenders == []
