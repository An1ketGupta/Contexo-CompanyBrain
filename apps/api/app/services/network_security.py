"""SSRF protection for outbound HTTP calls to user-supplied URLs.

Used by every code path that sends a request to a URL the user controls
(webhook delivery, agent API callbacks). Validates the URL's resolved
IP(s) against private/reserved ranges before the request fires.

Known limitation — DNS rebinding:
  A hostname can resolve to a public IP at validation time and a private
  IP at TCP-connect time. Mitigation: we resolve and validate ALL A/AAAA
  records returned by getaddrinfo (so simple round-robin DNS rebind
  fails because at least one record is private), and re-validate inside
  every call-site immediately before the request fires (windows the
  race to milliseconds, not seconds). Full pinned-IP delivery is tracked
  in TECH_DEBT.md as a post-launch hardening item.

No third-party deps — stdlib only. Adding a CIDR library would let an
audit-time supply-chain hit weaken the most security-critical helper in
the system.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# IP ranges we refuse to dial. Covers:
#   - RFC 1918 private (10/8, 172.16/12, 192.168/16)
#   - Loopback (127/8, ::1)
#   - Link-local incl. cloud metadata endpoint (169.254/16 — AWS IMDS,
#     GCP metadata.google.internal, Azure 169.254.169.254)
#   - Unique-local IPv6 (fc00::/7) and link-local IPv6 (fe80::/10)
#   - "This network" 0.0.0.0/8 — some kernels route 0.0.0.0 to localhost
#   - Multicast & broadcast — pointless destinations that some libs
#     accept and that can be used to probe internal infra
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),       # Carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),        # TEST-NET-1
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),       # Benchmark
    ipaddress.ip_network("198.51.100.0/24"),     # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),      # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),         # Multicast
    ipaddress.ip_network("240.0.0.0/4"),         # Reserved
    ipaddress.ip_network("255.255.255.255/32"),  # Broadcast
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::ffff:0:0/96"),        # IPv4-mapped IPv6 (defang via re-check)
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsafeURLError(Exception):
    """Raised when a URL is rejected by the SSRF guard.

    The exception message is safe to log but should NOT be returned to the
    caller verbatim — it confirms whether a specific IP is reachable from
    inside our network, which is itself useful recon for an attacker.
    """


def _ip_is_blocked(ip_str: str) -> tuple[bool, str | None]:
    """Return (blocked, reason). Reason is None when allowed."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True, f"unparseable_ip:{ip_str!r}"

    # IPv4-mapped IPv6 addresses (::ffff:10.0.0.1) need to be checked as
    # their IPv4 equivalent — otherwise an attacker can wrap a private
    # IPv4 in an IPv6 prefix to bypass the v4 blocklist.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    for network in _BLOCKED_NETWORKS:
        if ip.version != network.version:
            continue
        if ip in network:
            return True, f"in_blocked_range:{network}"
    return False, None


def validate_outbound_url(url: str) -> None:
    """Reject a URL if it would resolve to a private/reserved IP.

    Call immediately before every httpx request whose URL came from user
    input. Raises UnsafeURLError on rejection; returns None on success.
    """
    if not url or not isinstance(url, str):
        raise UnsafeURLError("empty_url")

    parsed = urlparse(url.strip())
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"scheme_not_allowed:{parsed.scheme!r}")
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("hostname_missing")

    # Reject IP literals up front against the blocklist before any DNS work.
    # Belt-and-braces: getaddrinfo would also return the literal, but
    # short-circuiting saves a syscall and avoids weird resolver behaviour
    # on platforms that try to "resolve" a literal.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        blocked, reason = _ip_is_blocked(str(literal_ip))
        if blocked:
            raise UnsafeURLError(f"ip_literal_blocked:{reason}")
        return

    try:
        # AI_ADDRCONFIG limits the result to address families the host
        # actually has — avoids resolving AAAA on an IPv4-only worker
        # and then comparing against the IPv6 blocklist for nothing.
        infos = socket.getaddrinfo(
            hostname,
            None,
            proto=socket.IPPROTO_TCP,
            flags=socket.AI_ADDRCONFIG,
        )
    except socket.gaierror as exc:
        # Don't allow unresolvable hostnames — partly because the caller's
        # retry loop will just thrash on it, partly because some resolvers
        # return private IPs for NXDOMAIN.
        raise UnsafeURLError(f"unresolvable:{hostname}") from exc

    if not infos:
        raise UnsafeURLError(f"no_address_records:{hostname}")

    # Validate EVERY resolved IP. If a hostname has both a public A and a
    # private A (cheap round-robin rebind), we refuse the whole hostname.
    for _family, _socktype, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        blocked, reason = _ip_is_blocked(ip_str)
        if blocked:
            raise UnsafeURLError(
                f"resolved_to_blocked_ip:{ip_str}:{reason}"
            )


def is_url_safe(url: str) -> bool:
    """Boolean wrapper for callers that want to branch without try/except."""
    try:
        validate_outbound_url(url)
    except UnsafeURLError:
        return False
    return True
