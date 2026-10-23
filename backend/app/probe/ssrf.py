"""SSRF guard: keep the probe away from private and internal addresses.

Rules:
1. Only http(s) URLs, with no username or password in them, on an allowed port.
2. Every address the hostname resolves to must be public (Python's `is_global` knows the
   private, loopback, link-local and reserved ranges).
3. The engine then connects to the address that was checked, not to the hostname, so a DNS
   answer that changes between the check and the connection cannot sneak through.
"""

import ipaddress
import logging
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

log = logging.getLogger(__name__)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

DEFAULT_PORTS = "80,443,8000-8999"
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class TargetError(Exception):
    """The URL is unusable: malformed, a forbidden scheme, credentials, or a blocked port."""


class BlockedTarget(Exception):
    """The URL or an address it resolves to is denied by policy."""


def parse_cidrs(spec: str) -> list[IPNetwork]:
    return [ipaddress.ip_network(c.strip()) for c in spec.split(",") if c.strip()]


def parse_ports(spec: str) -> list[tuple[int, int]]:
    """'80,443,8000-8999' -> [(80, 80), (443, 443), (8000, 8999)]."""
    out: list[tuple[int, int]] = []
    for part in (p.strip() for p in spec.split(",")):
        if part:
            low, _, high = part.partition("-")
            out.append((int(low), int(high or low)))
    return out


def _unwrap(addr: IPAddress) -> IPAddress:
    """An IPv4 address carried inside an IPv6 one is judged as that IPv4 address.

    ::ffff:127.0.0.1 (IPv4-mapped) and 64:ff9b::7f00:1 (NAT64) are both loopback in disguise.
    """
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return addr.ipv4_mapped
        if addr in _NAT64:
            return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return addr


def is_public(ip: str) -> bool:
    try:
        addr = _unwrap(ipaddress.ip_address(ip))
    except ValueError:
        return False
    return addr.is_global and not addr.is_multicast


@dataclass(frozen=True)
class Target:
    """A URL that passed the static checks (no DNS yet)."""

    host: str  # lower-case hostname or IP literal, without brackets
    is_ip_literal: bool
    parts: SplitResult


@dataclass(frozen=True)
class SsrfPolicy:
    # Dev/test escape hatch, e.g. so probes can reach Faultline on the Docker network.
    allow_private_cidrs: tuple[IPNetwork, ...] = ()
    # Ports a monitor may point at. Without this, a user could use the server to port-scan
    # other people's public hosts. None disables the check (tests use random local ports).
    allowed_ports: tuple[tuple[int, int], ...] | None = tuple(parse_ports(DEFAULT_PORTS))

    @classmethod
    def from_settings(cls, cidrs: str, ports: str = DEFAULT_PORTS) -> "SsrfPolicy":
        policy = cls(
            allow_private_cidrs=tuple(parse_cidrs(cidrs)), allowed_ports=tuple(parse_ports(ports))
        )
        if policy.allow_private_cidrs:
            log.warning(
                "SSRF_ALLOW_PRIVATE_CIDRS is set (%s): private ranges are reachable. "
                "This must be empty in production.",
                cidrs,
            )
        return policy

    def allows_ip(self, ip: str) -> bool:
        if is_public(ip):
            return True
        try:
            addr = _unwrap(ipaddress.ip_address(ip))
        except ValueError:
            return False
        return any(addr.version == net.version and addr in net for net in self.allow_private_cidrs)

    def validate_url(self, url: str) -> Target:
        """Static checks on the URL itself. Raises TargetError if it is unusable."""
        try:
            parts = urlsplit(url)
            port = parts.port
            host = parts.hostname
        except ValueError as exc:
            raise TargetError(f"malformed URL: {exc}") from exc

        if parts.scheme not in ("http", "https"):
            raise TargetError("scheme must be http or https")
        if parts.username is not None or parts.password is not None:
            raise TargetError("credentials in URL are not allowed")
        if not host:
            raise TargetError("URL has no host")
        port = port or (443 if parts.scheme == "https" else 80)
        if self.allowed_ports is not None and not any(
            low <= port <= high for low, high in self.allowed_ports
        ):
            raise TargetError(f"port {port} is not allowed")

        host = host.rstrip(".").lower()
        try:
            ipaddress.ip_address(host)
            is_ip = True
        except ValueError:
            is_ip = False
        return Target(host=host, is_ip_literal=is_ip, parts=parts)
