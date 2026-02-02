import unittest
from typing import Any, Dict, Optional
from unittest.mock import patch

import webtools_server.server as server


class FakeSearchResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return self._payload


class FakeSearchClient:
    def __init__(self, response: FakeSearchResponse) -> None:
        self._response = response
        self.request: Optional[Dict[str, Any]] = None

    async def __aenter__(self) -> "FakeSearchClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, params: Dict[str, Any], headers: Dict[str, str]):
        self.request = {"url": url, "params": params, "headers": headers}
        return self._response


class TestWebSearch(unittest.IsolatedAsyncioTestCase):
    async def test_web_search_clamps_num_results_and_maps_fields(self) -> None:
        payload = {
            "results": [
                {"title": f"Title {idx}", "url": f"http://ex/{idx}", "content": "c"}
                for idx in range(12)
            ]
        }
        response = FakeSearchResponse(payload)
        client = FakeSearchClient(response)

        with patch("webtools_server.server.httpx.AsyncClient", return_value=client):
            result = await server.web_search("example", num_results=50, language="en")

        self.assertEqual(len(result["results"]), 10)
        self.assertEqual(result["results"][0]["title"], "Title 0")
        self.assertEqual(result["results"][0]["snippet"], "c")
        self.assertEqual(client.request["params"]["q"], "example")

    async def test_web_search_min_results_is_one(self) -> None:
        payload = {"results": [{"title": "Only", "url": "http://ex/1"}]}
        response = FakeSearchResponse(payload)
        client = FakeSearchClient(response)

        with patch("webtools_server.server.httpx.AsyncClient", return_value=client):
            result = await server.web_search("example", num_results=0)

        self.assertEqual(len(result["results"]), 1)
