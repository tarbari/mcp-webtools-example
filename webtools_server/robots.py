import asyncio
from dataclasses import dataclass
from typing import Dict
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from webtools_server.config import USER_AGENT

_ROBOTS_TTL_SECONDS = 6 * 60 * 60


@dataclass
class RobotsCacheEntry:
    rp: robotparser.RobotFileParser
    fetched_at: float


_robots_cache: Dict[str, RobotsCacheEntry] = {}


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
