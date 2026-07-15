"""RTSP URL safety validator — blocks SSRF via private/reserved IP ranges.

Only rtsp:// and rtsps:// schemes are permitted. Bare IP addresses in
private, loopback, link-local, or carrier-grade-NAT ranges are rejected.
Hostnames that look like 'localhost' are also rejected. DNS-resolved
hostnames are NOT re-checked against IP ranges (that would require an async
DNS call and still be vulnerable to DNS rebinding); this validator is a
fast, synchronous guard against the obvious cases.
"""

import ipaddress
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"rtsp", "rtsps"})

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("127.0.0.0/8"),    # loopback
    ipaddress.ip_network("10.0.0.0/8"),     # RFC-1918 private
    ipaddress.ip_network("172.16.0.0/12"),  # RFC-1918 private
    ipaddress.ip_network("192.168.0.0/16"), # RFC-1918 private
    ipaddress.ip_network("169.254.0.0/16"), # link-local / AWS IMDS at 169.254.169.254
    ipaddress.ip_network("100.64.0.0/10"),  # carrier-grade NAT (RFC 6598)
    ipaddress.ip_network("0.0.0.0/8"),      # unspecified
    ipaddress.ip_network("::1/128"),        # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),       # IPv6 unique local (ULA)
    ipaddress.ip_network("fe80::/10"),      # IPv6 link-local
]

_BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def validate_rtsp_url(url: str) -> tuple[bool, str | None]:
    """Return (True, None) when the URL is safe to use; (False, reason) otherwise.

    Callers should raise HTTP 422 with the returned reason string.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL"

    if parsed.scheme not in ALLOWED_SCHEMES:
        allowed = ", ".join(sorted(ALLOWED_SCHEMES))
        return False, f"Scheme '{parsed.scheme or '(none)'}' is not allowed; use {allowed}"

    host = parsed.hostname
    if not host:
        return False, "URL contains no hostname"

    if host.lower() in _BLOCKED_HOSTNAMES:
        return False, f"Hostname '{host}' is not allowed"

    try:
        addr = ipaddress.ip_address(host)
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                return False, f"IP address {host} is in a reserved range and cannot be used as a camera host"
    except ValueError:
        pass  # hostname, not a bare IP — accepted

    return True, None
