from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Sequence
from urllib.parse import urlsplit

from dr_core.normalization import InvalidURL, normalize_url
from dr_core.providers.errors import UnsafeUrlError

Resolver = Callable[[str, int], Sequence[str]]

_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".home.arpa",
    ".lan",
    ".intranet",
    ".corp",
)
_BLOCKED_HOSTS = {"localhost", "metadata", "instance-data"}
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)
_ALLOWED_PORTS = {80, 443}


def default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
    )
    return [str(info[4][0]) for info in infos]


def _is_blocked_ip(value: str) -> bool:
    candidate = value.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return True
    addresses = [address]
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        addresses.append(address.ipv4_mapped)
    for item in addresses:
        if (
            not item.is_global
            or item.is_private
            or item.is_loopback
            or item.is_link_local
            or item.is_multicast
            or item.is_reserved
            or item.is_unspecified
        ):
            return True
        if any(
            item.version == network.version and item in network
            for network in _BLOCKED_NETWORKS
        ):
            return True
    return False


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def validate_fetch_url(
    url: str,
    *,
    resolver: Resolver | None = None,
    resolve_dns: bool = True,
) -> str:
    """Reject URLs that must never reach a fetcher.

    ``resolve_dns`` must stay enabled when the process itself opens the
    connection: the resolved address is what decides whether the request is
    private. A remote fetch provider resolves the host on its own network, so
    local DNS (which on this deployment maps public hosts into a proxy fake-IP
    range) would only produce false rejections.
    """
    try:
        normalized = normalize_url(url)
    except InvalidURL as exc:
        raise UnsafeUrlError("url", str(exc)) from exc

    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeUrlError("url", "URL must include a hostname")
    if parsed.port is not None and parsed.port not in _ALLOWED_PORTS:
        raise UnsafeUrlError("url", "URL port is not allowed")

    if "%" in host or host in _BLOCKED_HOSTS:
        raise UnsafeUrlError("url", "URL hostname is not allowed")
    if any(host.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        raise UnsafeUrlError("url", "URL hostname is not allowed")
    if _is_ip_literal(host):
        if _is_blocked_ip(host):
            raise UnsafeUrlError(
                "url",
                "URL resolves to a private or reserved address",
            )
        return normalized
    if "." not in host:
        raise UnsafeUrlError("url", "Single-label hostnames are not allowed")
    if not resolve_dns:
        return normalized

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        resolved = list((resolver or default_resolver)(host, port))
    except OSError as exc:
        raise UnsafeUrlError("url", "URL hostname could not be resolved") from exc
    if not resolved:
        raise UnsafeUrlError("url", "URL hostname did not resolve to an address")
    if any(_is_blocked_ip(address) for address in resolved):
        raise UnsafeUrlError("url", "URL resolves to a private or reserved address")

    return normalized
