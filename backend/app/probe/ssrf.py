"""SSRF guard: URL validation + IP classification.

Pure functions and a small policy object; no I/O. The engine resolves the hostname, asks
the policy about *every* resolved address, then connects to one validated IP (pinning).
"""

import ipaddress
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit

log = logging.getLogger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

DEFAULT_PORTS = "80,443,8000-8999"

_BLOCKED_V4 = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",  # "this" network
        "10.0.0.0/8",  # private
        "100.64.0.0/10",  # carrier-grade NAT
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local, incl. cloud metadata 169.254.169.254
        "172.16.0.0/12",  # private
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # TEST-NET-1
        "192.88.99.0/24",  # deprecated 6to4 relay
        "192.168.0.0/16",  # private
        "198.18.0.0/15",  # benchmarking
        "198.51.100.0/24",  # TEST-NET-2
        "203.0.113.0/24",  # TEST-NET-3
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved + 255.255.255.255 broadcast
    )
]
_BLOCKED_V6 = [
    ipaddress.ip_network(n)
    for n in (
        "::/96",  # unspecified, loopback and deprecated IPv4-compatible
        "64:ff9b:1::/48",  # local-use NAT64
        "100::/64",  # discard-only
        "2001::/32",  # Teredo
        "2001:db8::/32",  # documentation
        "fc00::/7",  # unique local
        "fe80::/10",  # link-local
        "ff00::/8",  # multicast
    )
]
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIXTO4 = ipaddress.ip_network("2002::/16")

_BAD_HOST_SUFFIXES = (".localhost", ".local", ".internal")


class TargetError(Exception):
    """The URL is malformed (scheme, userinfo, empty host): config error, not policy."""


class BlockedTarget(Exception):
    """The URL or an address it resolves to is denied by policy."""


def parse_ports(spec: str) -> list[tuple[int, int]]:
    """'80,443,8000-8999' -> [(80, 80), (443, 443), (8000, 8999)]."""
    out: list[tuple[int, int]] = []
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        out.append((int(lo), int(hi or lo)))
    return out


def parse_cidrs(spec: str) -> list[IPNetwork]:
    return [ipaddress.ip_network(c.strip()) for c in spec.split(",") if c.strip()]


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """IPv4 carried inside an IPv6 address by mapping / NAT64 / 6to4, else None."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in _SIXTO4:
        return ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    return None


def is_ip_allowed(ip: str | IPAddress, allow_cidrs: Iterable[IPNetwork] = ()) -> bool:
    """True when connecting to `ip` is allowed.

    IPv6 forms that embed an IPv4 address are unwrapped and judged as that IPv4, so
    ::ffff:127.0.0.1 is as blocked as 127.0.0.1. `allow_cidrs` is the dev/test escape hatch.
    """
    try:
        addr = ipaddress.ip_address(ip) if isinstance(ip, str) else ip
    except ValueError:
        return False

    if isinstance(addr, ipaddress.IPv6Address):
        inner = _embedded_ipv4(addr)
        if inner is not None:
            return is_ip_allowed(inner, allow_cidrs)

    for net in allow_cidrs:
        if addr.version == net.version and addr in net:
            return True

    blocked = _BLOCKED_V4 if addr.version == 4 else _BLOCKED_V6
    if any(addr in net for net in blocked):
        return False
    # Belt and braces: anything the stdlib does not consider globally routable.
    return addr.is_global


_NUMERIC_LABEL = re.compile(r"^(0x[0-9a-f]+|[0-9]+)$", re.IGNORECASE)


def _normalise_host(host: str) -> str:
    host = host.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise TargetError(f"invalid host: {exc}") from exc


@dataclass(frozen=True)
class Target:
    """A URL that passed the static checks (no DNS yet)."""

    url: str
    scheme: str
    host: str  # normalised hostname or IP literal (no brackets)
    port: int
    is_ip_literal: bool
    parts: SplitResult


@dataclass(frozen=True)
class SsrfPolicy:
    allowed_ports: tuple[tuple[int, int], ...] | None = tuple(parse_ports(DEFAULT_PORTS))
    allow_private_cidrs: tuple[IPNetwork, ...] = ()
    allowed_hosts: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_settings(cls, ports: str, cidrs: str, hosts: str = "") -> "SsrfPolicy":
        policy = cls(
            allowed_ports=tuple(parse_ports(ports)),
            allow_private_cidrs=tuple(parse_cidrs(cidrs)),
            allowed_hosts=frozenset(h.strip().lower() for h in hosts.split(",") if h.strip()),
        )
        if policy.allow_private_cidrs:
            log.warning(
                "SSRF_ALLOW_PRIVATE_CIDRS is set (%s): private ranges are reachable. "
                "This must be empty in production.",
                cidrs,
            )
        return policy

    def allows_ip(self, ip: str) -> bool:
        return is_ip_allowed(ip, self.allow_private_cidrs)

    def validate_url(self, url: str) -> Target:
        """Static checks. Raises TargetError (malformed) or BlockedTarget (denied)."""
        try:
            parts = urlsplit(url)
            raw_port = parts.port
            raw_host = parts.hostname
        except ValueError as exc:
            raise TargetError(f"malformed URL: {exc}") from exc

        if parts.scheme not in ("http", "https"):
            raise TargetError("scheme must be http or https")
        if parts.username is not None or parts.password is not None:
            raise TargetError("credentials in URL are not allowed")
        if not raw_host:
            raise TargetError("URL has no host")

        host = _normalise_host(raw_host)
        port = raw_port or (443 if parts.scheme == "https" else 80)
        if self.allowed_ports is not None and not any(
            lo <= port <= hi for lo, hi in self.allowed_ports
        ):
            raise BlockedTarget(f"port {port} is not allowed")

        try:
            ipaddress.ip_address(host)
            is_ip = True
        except ValueError:
            is_ip = False

        if not is_ip:
            labels = host.split(".")
            # "2130706433", "0x7f.1", "017700000001": some libraries read these as IPv4.
            # A DNS name's last label is never purely numeric, so reject rather than guess.
            if _NUMERIC_LABEL.match(labels[-1]):
                raise BlockedTarget("numeric host is not a canonical IP address")
            if host not in self.allowed_hosts:
                if len(labels) < 2 or host == "localhost" or host.endswith(_BAD_HOST_SUFFIXES):
                    raise BlockedTarget("internal-looking hostname")
        return Target(
            url=url, scheme=parts.scheme, host=host, port=port, is_ip_literal=is_ip, parts=parts
        )
