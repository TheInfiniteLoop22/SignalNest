import ipaddress

import pytest

from app.probe.ssrf import SsrfPolicy, TargetError, is_public, parse_cidrs, parse_ports

# --- which addresses count as public: (address, public?) ------------------------------

IP_CASES = [
    # public
    ("93.184.216.34", True),
    ("8.8.8.8", True),
    ("172.32.0.1", True),  # just outside 172.16/12
    ("2606:2800:220:1:248:1893:25c8:1946", True),
    # loopback and "this host"
    ("127.0.0.1", False),
    ("0.0.0.0", False),
    ("::1", False),
    # private ranges
    ("10.0.0.1", False),
    ("172.16.0.1", False),
    ("192.168.1.1", False),
    ("fd12:3456::1", False),
    # link-local, including the cloud metadata address
    ("169.254.169.254", False),
    ("fe80::1", False),
    # carrier-grade NAT, multicast, reserved
    ("100.64.0.1", False),
    ("224.0.0.1", False),
    ("255.255.255.255", False),
    # IPv4 hidden inside IPv6 is judged as the IPv4 address
    ("::ffff:127.0.0.1", False),
    ("::ffff:169.254.169.254", False),
    ("::ffff:93.184.216.34", True),
    # NAT64 (64:ff9b::/96) carries an IPv4 address in its last 32 bits
    ("64:ff9b::7f00:1", False),  # 127.0.0.1
    ("64:ff9b::a00:1", False),  # 10.0.0.1
    ("64:ff9b::a9fe:a9fe", False),  # 169.254.169.254
    ("64:ff9b::808:808", True),  # 8.8.8.8
    # garbage
    ("not-an-ip", False),
    ("", False),
]


@pytest.mark.parametrize(("ip", "public"), IP_CASES)
def test_is_public(ip: str, public: bool) -> None:
    assert is_public(ip) is public


def test_allow_cidr_override_for_dev() -> None:
    policy = SsrfPolicy(allow_private_cidrs=tuple(parse_cidrs("172.16.0.0/12, 127.0.0.0/8")))
    assert policy.allows_ip("172.18.0.5")
    assert policy.allows_ip("127.0.0.1")
    assert policy.allows_ip("93.184.216.34")  # public stays allowed
    assert not policy.allows_ip("10.0.0.1")  # private but not in the override
    assert not policy.allows_ip("169.254.169.254")


def test_default_policy_allows_only_public_addresses() -> None:
    policy = SsrfPolicy()
    assert policy.allows_ip("8.8.8.8")
    assert not policy.allows_ip("10.0.0.1")


def test_override_does_not_leak_across_ip_versions() -> None:
    policy = SsrfPolicy(allow_private_cidrs=tuple(parse_cidrs("127.0.0.0/8")))
    assert not policy.allows_ip("::1")


def test_parse_cidrs() -> None:
    assert parse_cidrs("") == []
    assert parse_cidrs("10.0.0.0/8") == [ipaddress.ip_network("10.0.0.0/8")]


# --- URL validation ---------------------------------------------------------------------


def test_a_normal_url_is_accepted() -> None:
    target = SsrfPolicy().validate_url("https://Example.com./path?q=1")
    assert target.host == "example.com"
    assert not target.is_ip_literal


def test_ip_literals_are_flagged() -> None:
    assert SsrfPolicy().validate_url("http://127.0.0.1:8080/").is_ip_literal
    assert SsrfPolicy().validate_url("http://[::1]/").is_ip_literal


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "file:///etc/passwd",
        "gopher://example.com/",
        "http://user:pw@example.com/",
        "http://",
        "notaurl",
        "http://example.com:notaport/",
    ],
)
def test_unusable_urls_are_rejected(url: str) -> None:
    with pytest.raises(TargetError):
        SsrfPolicy().validate_url(url)


def test_allow_list_also_matches_ipv4_hidden_in_ipv6() -> None:
    policy = SsrfPolicy(allow_private_cidrs=tuple(parse_cidrs("127.0.0.0/8")))
    assert policy.allows_ip("::ffff:127.0.0.1")
    assert not policy.allows_ip("::ffff:10.0.0.1")


# --- ports ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",  # 80
        "https://example.com/",  # 443
        "http://example.com:8000/",
        "http://example.com:8080/",
        "http://example.com:8999/",
        "https://example.com:443/",
    ],
)
def test_default_ports_are_accepted(url: str) -> None:
    SsrfPolicy().validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:22/",
        "http://example.com:25/",
        "http://example.com:3306/",
        "http://example.com:5432/",
        "http://example.com:6379/",
        "http://example.com:7999/",
        "http://example.com:9000/",
        "https://example.com:9443/",
    ],
)
def test_other_ports_are_refused(url: str) -> None:
    with pytest.raises(TargetError, match="not allowed"):
        SsrfPolicy().validate_url(url)


def test_ports_can_be_configured() -> None:
    policy = SsrfPolicy.from_settings("", "443,9000-9100")
    policy.validate_url("https://example.com/")
    policy.validate_url("http://example.com:9050/")
    with pytest.raises(TargetError):
        policy.validate_url("http://example.com/")  # port 80 is no longer listed


def test_the_port_check_can_be_disabled_for_tests() -> None:
    SsrfPolicy(allowed_ports=None).validate_url("http://example.com:22/")


def test_parse_ports() -> None:
    assert parse_ports("80, 443,8000-8999") == [(80, 80), (443, 443), (8000, 8999)]
    assert parse_ports("") == []
