import asyncio
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import urlparse
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

BLOCK_PRIVATE_NETS = True

mcp = FastMCP(
    "Web Tools",
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "localhost:*",
            "127.0.0.1:*",
        ],
        allowed_origins=[
            "http://localhost:*",
            "http://127.0.0.1:*",
        ],
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
        ip = info[4][0]
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
    now = asyncio.get_event_loop().time()

    entry = _robots_cache.get(base)
    if entry and (now - entry.fetched_at) < _ROBOTS_TTL_SECONDS:
        return entry.rp.can_fetch(USER_AGENT, url)

    rp = robotparser.RobotFileParser()
    try:
        r = await client.get(robots_url, headers={"User-Agent": USER_AGENT, "X-Real-IP": "127.0.0.1"})
        if r.status_code >= 400:
            rp.parse([])  # allow
        else:
            rp.parse(r.text.splitlines())
    except Exception:
        rp.parse([])  # allow on failure (switch to fail-closed if you prefer)

    _robots_cache[base] = RobotsCacheEntry(rp=rp, fetched_at=now)
    return rp.can_fetch(USER_AGENT, url)

# ---------------- Tools ----------------
@mcp.tool()
async def web_search(query: str, num_results: int = 5, language: str = "en") -> Dict[str, Any]:
    num_results = max(1, min(num_results, 10))
    params = {"q": query, "format": "json", "language": language, "pageno": 1, }

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
        r = await client.get(SEARXNG_URL, params=params, headers={"User-Agent": USER_AGENT})
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
    _validate_public_url(url)

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
        allowed = await _can_fetch(url, client)
        if not allowed:
            return {"url": url, "blocked": True, "reason": "Blocked by robots.txt for this User-Agent"}

        r = await client.get(url, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()

        content_type = r.headers.get("content-type", "")
        raw = r.content[:MAX_PAGE_BYTES]
        html = raw.decode(r.encoding or "utf-8", errors="replace")

    try:
        import trafilatura
        extracted = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    except Exception:
        extracted = ""

    extracted = extracted.strip()
    truncated = len(extracted) > MAX_EXTRACT_CHARS
    if truncated:
        extracted = extracted[:MAX_EXTRACT_CHARS]

    title = ""
    if "<title" in html.lower():
        import re
        m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()

    return {
        "url": url,
        "title": title,
        "content_type": content_type,
        "extracted_text": extracted,
        "text_truncated": truncated,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "chars": len(extracted),
    }

@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    # Required for Streamable HTTP session management
    async with mcp.session_manager.run():
        yield

# Mount MCP at /mcp (default path). If you mount at "/", it will be at /mcp due to default pathing.
app = Starlette(
    routes=[
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

