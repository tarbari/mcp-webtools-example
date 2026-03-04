from typing import Any, Dict

import httpx

from webtools_server.app import mcp
from webtools_server.config import FETCH_TIMEOUT, SEARXNG_URL, USER_AGENT
from webtools_server.helpers import _error_response


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
