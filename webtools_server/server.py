import asyncio
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urljoin
from urllib import robotparser

import contextlib
from starlette.applications import Starlette
from starlette.routing import Mount
import uvicorn

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


# ---- Config ----
SEARXNG_URL = "http://searxng:8080/search"
USER_AGENT = "mcp-webtools/0.1 (respect-robots)"
MAX_PAGE_BYTES = 2_500_000
MAX_EXTRACT_CHARS = 30_000
FETCH_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=10.0)
MAX_REDIRECTS = 5

BLOCK_PRIVATE_NETS = True


def _split_env_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_allowed_entries(env_key: str) -> list[str]:
    value = os.getenv(env_key, "")
    return _split_env_list(value) if value else []


DEFAULT_ALLOWED_HOSTS = [
    "localhost:*",
    "127.0.0.1:*",
]

DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:*",
    "http://127.0.0.1:*",
]

allowed_hosts = DEFAULT_ALLOWED_HOSTS + _load_allowed_entries("MCP_ALLOWED_HOSTS")
allowed_origins = DEFAULT_ALLOWED_ORIGINS + _load_allowed_entries("MCP_ALLOWED_ORIGINS")

mcp = FastMCP(
    "Web Tools",
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    ),
)


# ---------------- Security helpers ----------------
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


# ---------------- Robots.txt handling ----------------
@dataclass
class RobotsCacheEntry:
    rp: robotparser.RobotFileParser
    fetched_at: float


_robots_cache: Dict[str, RobotsCacheEntry] = {}
_ROBOTS_TTL_SECONDS = 6 * 60 * 60


async def _can_fetch(url: str, client: httpx.AsyncClient) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = f"{base}/robots.txt"
    now = asyncio.get_running_loop().time()

    entry = _robots_cache.get(base)
    if entry and (now - entry.fetched_at) < _ROBOTS_TTL_SECONDS:
        return entry.rp.can_fetch(USER_AGENT, url)

    rp = robotparser.RobotFileParser()
    try:
        r = await client.get(
            robots_url, headers={"User-Agent": USER_AGENT, "X-Real-IP": "127.0.0.1"}
        )
        if r.status_code >= 400:
            rp.parse([])  # allow
        else:
            rp.parse(r.text.splitlines())
    except Exception:
        rp.parse([])  # allow on failure (switch to fail-closed if you prefer)

    _robots_cache[base] = RobotsCacheEntry(rp=rp, fetched_at=now)
    return rp.can_fetch(USER_AGENT, url)


def _error_response(
    *,
    error_type: str,
    message: str,
    url: str,
    final_url: Optional[str] = None,
    status_code: Optional[int] = None,
    blocked_reason: Optional[str] = None,
    content_type: Optional[str] = None,
    content_length: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "url": url,
        "final_url": final_url or url,
        "error": {
            "type": error_type,
            "message": message,
            "status_code": status_code,
            "blocked_reason": blocked_reason,
            "content_type": content_type,
            "content_length": content_length,
        },
    }


# ---------------- Tools ----------------
@mcp.tool()
async def web_search(
    query: str, num_results: int = 5, language: str = "en"
) -> Dict[str, Any]:
    num_results = max(1, min(num_results, 10))
    params = {
        "q": query,
        "format": "json",
        "language": language,
        "pageno": 1,
    }

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
        r = await client.get(
            SEARXNG_URL, params=params, headers={"User-Agent": USER_AGENT}
        )
        r.raise_for_status()
        data = r.json()

    results = []
    for item in (data.get("results") or [])[:num_results]:
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("content") or item.get("snippet"),
                "engine": item.get("engine"),
            }
        )
    return {"query": query, "results": results}


@mcp.tool()
async def fetch_url(url: str) -> Dict[str, Any]:
    try:
        _validate_public_url(url)
    except ValueError as exc:
        return _error_response(error_type="invalid_url", message=str(exc), url=url)

    start_time = time.monotonic()
    current_url = url
    robots_allowed = True
    robots_reason = ""

    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT, follow_redirects=False
    ) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                _validate_public_url(current_url)
            except ValueError as exc:
                return _error_response(
                    error_type="blocked",
                    message=str(exc),
                    url=url,
                    final_url=current_url,
                    blocked_reason="blocked_hostname_or_ip",
                )

            robots_allowed = await _can_fetch(current_url, client)
            if not robots_allowed:
                robots_reason = "Blocked by robots.txt for this User-Agent"
                return _error_response(
                    error_type="blocked",
                    message=robots_reason,
                    url=url,
                    final_url=current_url,
                    blocked_reason="robots",
                )

            status_code = None
            content_type = ""
            encoding = "utf-8"

            try:
                async with client.stream(
                    "GET", current_url, headers={"User-Agent": USER_AGENT}
                ) as r:
                    status_code = r.status_code
                    if status_code in (301, 302, 303, 307, 308):
                        location = r.headers.get("location")
                        if not location:
                            return _error_response(
                                error_type="redirect_error",
                                message="Redirect response missing Location header.",
                                url=url,
                                final_url=current_url,
                                status_code=status_code,
                            )
                        current_url = urljoin(current_url, location)
                        try:
                            _validate_public_url(current_url)
                        except ValueError as exc:
                            return _error_response(
                                error_type="blocked",
                                message=str(exc),
                                url=url,
                                final_url=current_url,
                                blocked_reason="redirect_blocked_hostname_or_ip",
                            )
                        continue

                    if status_code >= 400:
                        return _error_response(
                            error_type="http_error",
                            message="HTTP request failed.",
                            url=url,
                            final_url=current_url,
                            status_code=status_code,
                        )

                    content_type = r.headers.get("content-type", "")
                    content_length_header = r.headers.get("content-length")
                    if content_length_header:
                        try:
                            content_length = int(content_length_header)
                        except ValueError:
                            content_length = None
                        if content_length and content_length > MAX_PAGE_BYTES:
                            return _error_response(
                                error_type="too_large",
                                message="Content-Length exceeds maximum allowed size.",
                                url=url,
                                final_url=current_url,
                                status_code=status_code,
                                content_type=content_type,
                                content_length=content_length,
                            )

                    raw = bytearray()
                    bytes_truncated = False
                    bytes_read = 0
                    async for chunk in r.aiter_bytes():
                        if not chunk:
                            continue
                        remaining = MAX_PAGE_BYTES - bytes_read
                        if remaining <= 0:
                            bytes_truncated = True
                            break
                        if len(chunk) > remaining:
                            raw.extend(chunk[:remaining])
                            bytes_read += remaining
                            bytes_truncated = True
                            break
                        raw.extend(chunk)
                        bytes_read += len(chunk)

                    encoding = r.encoding or "utf-8"
                    normalized_type = content_type.lower()
                    is_html = "text/html" in normalized_type or not normalized_type
                    is_text = normalized_type.startswith("text/plain")

                    if not (is_html or is_text):
                        return _error_response(
                            error_type="unsupported_content_type",
                            message="Content-Type is not supported for text extraction.",
                            url=url,
                            final_url=current_url,
                            status_code=status_code,
                            content_type=content_type,
                        )

                    html = raw.decode(encoding, errors="replace")
            except httpx.HTTPError as exc:
                return _error_response(
                    error_type="network_error",
                    message=str(exc),
                    url=url,
                    final_url=current_url,
                )

            try:
                import trafilatura

                extracted = (
                    trafilatura.extract(
                        html, include_comments=False, include_tables=False
                    )
                    or ""
                )
            except Exception:
                extracted = ""

            extracted = extracted.strip()
            text_truncated = len(extracted) > MAX_EXTRACT_CHARS
            if text_truncated:
                extracted = extracted[:MAX_EXTRACT_CHARS]

            title = ""
            if "<title" in html.lower():
                import re

                m = re.search(
                    r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL
                )
                if m:
                    title = re.sub(r"\s+", " ", m.group(1)).strip()

            fetch_ms = int((time.monotonic() - start_time) * 1000)
            return {
                "url": url,
                "final_url": current_url,
                "status_code": status_code,
                "title": title,
                "content_type": content_type,
                "encoding": encoding,
                "extracted_text": extracted,
                "text_truncated": text_truncated,
                "bytes": bytes_read,
                "bytes_truncated": bytes_truncated,
                "robots_allowed": robots_allowed,
                "robots_reason": robots_reason,
                "fetch_ms": fetch_ms,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "chars": len(extracted),
            }

        return _error_response(
            error_type="redirect_limit",
            message="Maximum redirects exceeded.",
            url=url,
            final_url=current_url,
        )


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    # Required for Streamable HTTP session management
    async with mcp.session_manager.run():
        yield


PDF_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)


def _validate_pdf_url(url: str) -> None:
    """Validate URL for PDF fetching (reuses existing security checks)."""
    _validate_public_url(url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are allowed.")


@mcp.tool()
async def fetch_pdf(url: str) -> Dict[str, Any]:
    """Fetch and extract text content from a PDF document."""
    try:
        _validate_pdf_url(url)
    except ValueError as exc:
        return _error_response(error_type="invalid_url", message=str(exc), url=url)

    start_time = time.monotonic()
    current_url = url
    status_code = None

    async with httpx.AsyncClient(
        timeout=PDF_TIMEOUT, follow_redirects=False
    ) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                _validate_pdf_url(current_url)
            except ValueError as exc:
                return _error_response(
                    error_type="blocked",
                    message=str(exc),
                    url=url,
                    final_url=current_url,
                    blocked_reason="blocked_hostname_or_ip",
                )

            try:
                async with client.stream(
                    "GET", current_url, headers={"User-Agent": USER_AGENT}
                ) as r:
                    status_code = r.status_code
                    if status_code in (301, 302, 303, 307, 308):
                        location = r.headers.get("location")
                        if not location:
                            return _error_response(
                                error_type="redirect_error",
                                message="Redirect response missing Location header.",
                                url=url,
                                final_url=current_url,
                                status_code=status_code,
                            )
                        current_url = urljoin(current_url, location)
                        try:
                            _validate_pdf_url(current_url)
                        except ValueError as exc:
                            return _error_response(
                                error_type="blocked",
                                message=str(exc),
                                url=url,
                                final_url=current_url,
                                blocked_reason="redirect_blocked_hostname_or_ip",
                            )
                        continue

                    if status_code >= 400:
                        return _error_response(
                            error_type="http_error",
                            message="HTTP request failed.",
                            url=url,
                            final_url=current_url,
                            status_code=status_code,
                        )

                    content_type = r.headers.get("content-type", "")
                    normalized_type = content_type.lower()
                    
                    if "application/pdf" not in normalized_type:
                        return _error_response(
                            error_type="unsupported_content_type",
                            message=f"Content-Type is not PDF: {content_type}",
                            url=url,
                            final_url=current_url,
                            status_code=status_code,
                            content_type=content_type,
                        )

                    content_length_header = r.headers.get("content-length")
                    if content_length_header:
                        try:
                            content_length = int(content_length_header)
                        except ValueError:
                            content_length = None
                        if content_length and content_length > MAX_PAGE_BYTES:
                            return _error_response(
                                error_type="too_large",
                                message="Content-Length exceeds maximum allowed size.",
                                url=url,
                                final_url=current_url,
                                status_code=status_code,
                                content_type=content_type,
                                content_length=content_length,
                            )

                    raw = bytearray()
                    bytes_truncated = False
                    bytes_read = 0
                    extracted = ""
                    pages = 0
                    pdf_reader = None
                    
                    async for chunk in r.aiter_bytes():
                        if not chunk:
                            continue
                        remaining = MAX_PAGE_BYTES - bytes_read
                        if remaining <= 0:
                            bytes_truncated = True
                            break
                        if len(chunk) > remaining:
                            raw.extend(chunk[:remaining])
                            bytes_read += remaining
                            bytes_truncated = True
                            break
                        raw.extend(chunk)
                        bytes_read += len(chunk)

                    try:
                        import pypdf

                        pdf_reader = pypdf.PdfReader(raw)
                        pages = len(pdf_reader.pages)
                        
                        extracted_parts = []
                        for page in pdf_reader.pages:
                            try:
                                text = page.extract_text() or ""
                                extracted_parts.append(text)
                            except Exception:
                                continue
                        
                        extracted = "\n".join(extracted_parts).strip()
                    except pypdf.errors.PdfReadError as exc:
                        if "encrypted" in str(exc).lower():
                            return _error_response(
                                error_type="pdf_password_protected",
                                message="PDF is password-protected.",
                                url=url,
                                final_url=current_url,
                                status_code=status_code,
                                content_type=content_type,
                            )
                        return _error_response(
                            error_type="pdf_parse_error",
                            message=f"Failed to parse PDF: {str(exc)}",
                            url=url,
                            final_url=current_url,
                            status_code=status_code,
                            content_type=content_type,
                        )
                    except Exception as exc:
                        return _error_response(
                            error_type="pdf_parse_error",
                            message=f"Failed to extract text from PDF: {str(exc)}",
                            url=url,
                            final_url=current_url,
                            status_code=status_code,
                            content_type=content_type,
                        )

            except httpx.HTTPError as exc:
                return _error_response(
                    error_type="network_error",
                    message=str(exc),
                    url=url,
                    final_url=current_url,
                )
            text_truncated = len(extracted) > MAX_EXTRACT_CHARS
            if text_truncated:
                extracted = extracted[:MAX_EXTRACT_CHARS]

            fetch_ms = int((time.monotonic() - start_time) * 1000)
            
            metadata = {}
            try:
                info = pdf_reader.metadata
                if info:
                    for key, value in info.items():
                        if value:
                            clean_key = key.replace("/", "").lower()
                            metadata[clean_key] = str(value)
            except Exception:
                pass

            return {
                "url": url,
                "final_url": current_url,
                "status_code": status_code,
                "content_type": content_type,
                "extracted_text": extracted,
                "text_truncated": text_truncated,
                "pages": pages,
                "metadata": metadata,
                "bytes": bytes_read,
                "bytes_truncated": bytes_truncated,
                "fetch_ms": fetch_ms,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "chars": len(extracted),
            }

        return _error_response(
            error_type="redirect_limit",
            message="Maximum redirects exceeded.",
            url=url,
            final_url=current_url,
        )


# Mount MCP at /mcp (default path). If you mount at "/", it will be at /mcp due to default pathing.
app = Starlette(
    routes=[
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
