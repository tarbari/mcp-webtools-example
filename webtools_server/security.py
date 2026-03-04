import socket
from urllib.parse import urlparse

from webtools_server.config import BLOCK_PRIVATE_NETS


def _is_ip_private_or_local(ip: str) -> bool:
    import ipaddress

    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _hostname_points_to_blocked_ip(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return True
    for info in infos:
        ip = str(info[4][0])
        if _is_ip_private_or_local(ip):
            return True
    return False


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are allowed.")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname.")
    if BLOCK_PRIVATE_NETS:
        if parsed.hostname in ("localhost",):
            raise ValueError("Blocked hostname.")
        if parsed.hostname == "169.254.169.254":
            raise ValueError("Blocked hostname.")
        if _hostname_points_to_blocked_ip(parsed.hostname):
            raise ValueError("Hostname resolves to a private/local IP (blocked).")


def _validate_pdf_url(url: str) -> None:
    """Validate URL for PDF fetching (reuses existing security checks)."""
    _validate_public_url(url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are allowed.")
