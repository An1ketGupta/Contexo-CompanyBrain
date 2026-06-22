"""Unit tests for the SSRF guard.

These are pure-function tests against ``validate_outbound_url`` — no
network is touched for the literal-IP cases. The DNS-based cases stub
``socket.getaddrinfo`` so the suite stays hermetic.
"""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from app.services.network_security import (
    UnsafeURLError,
    is_url_safe,
    validate_outbound_url,
)


# ── Literal-IP rejections (no DNS needed) ─────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",          # AWS IMDS
        "http://169.254.169.254/computeMetadata/v1/",        # GCP metadata
        "http://127.0.0.1:8000/admin",
        "http://127.1/admin",
        "http://localhost:6379/",                            # resolves to loopback
        "http://10.0.0.5/secret",
        "http://10.255.255.255/",
        "http://172.16.0.1/",
        "http://172.31.255.254/",
        "http://192.168.1.1/",
        "http://0.0.0.0/",
        "http://255.255.255.255/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        # IPv4-mapped IPv6 must defang to the v4 equivalent and re-check.
        "http://[::ffff:169.254.169.254]/",
        "http://[::ffff:a00:1]/",                            # ::ffff:10.0.0.1
    ],
)
def test_blocks_private_and_metadata_addresses(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_outbound_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "gopher://example.com/_",
        "javascript:alert(1)",
        "",
    ],
)
def test_rejects_non_http_schemes_and_empty(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_outbound_url(url)


def test_rejects_url_without_hostname() -> None:
    with pytest.raises(UnsafeURLError):
        validate_outbound_url("http:///path-but-no-host")


# ── DNS-mediated rejections (stubbed resolver) ────────────────────────────────


def _fake_getaddrinfo(ips: list[tuple[int, str]]):
    """Build a fake getaddrinfo result list out of (family, ip) tuples."""
    return [
        (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))
        for family, ip in ips
    ]


def test_blocks_hostname_resolving_to_private_ip() -> None:
    with patch(
        "app.services.network_security.socket.getaddrinfo",
        return_value=_fake_getaddrinfo([(socket.AF_INET, "10.0.0.5")]),
    ):
        with pytest.raises(UnsafeURLError):
            validate_outbound_url("https://attacker-controlled.example/")


def test_blocks_hostname_with_mixed_public_and_private_records() -> None:
    """Round-robin DNS rebind: one A record public, one private. We
    refuse the whole hostname rather than racing the resolver."""
    with patch(
        "app.services.network_security.socket.getaddrinfo",
        return_value=_fake_getaddrinfo(
            [
                (socket.AF_INET, "8.8.8.8"),
                (socket.AF_INET, "192.168.0.1"),
            ]
        ),
    ):
        with pytest.raises(UnsafeURLError):
            validate_outbound_url("https://rebinding.example/")


def test_allows_public_resolved_hostname() -> None:
    with patch(
        "app.services.network_security.socket.getaddrinfo",
        return_value=_fake_getaddrinfo([(socket.AF_INET, "8.8.8.8")]),
    ):
        validate_outbound_url("https://dns.google/")  # no raise


def test_rejects_unresolvable_hostname() -> None:
    with patch(
        "app.services.network_security.socket.getaddrinfo",
        side_effect=socket.gaierror("name does not resolve"),
    ):
        with pytest.raises(UnsafeURLError):
            validate_outbound_url("https://does-not-resolve.invalid/")


def test_rejects_empty_address_records() -> None:
    with patch(
        "app.services.network_security.socket.getaddrinfo",
        return_value=[],
    ):
        with pytest.raises(UnsafeURLError):
            validate_outbound_url("https://empty.example/")


def test_is_url_safe_returns_boolean() -> None:
    assert is_url_safe("http://10.0.0.1/") is False
    with patch(
        "app.services.network_security.socket.getaddrinfo",
        return_value=_fake_getaddrinfo([(socket.AF_INET, "1.1.1.1")]),
    ):
        assert is_url_safe("https://one.one.one.one/") is True
