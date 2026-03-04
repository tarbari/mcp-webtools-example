import types
import unittest
from typing import AsyncIterator, Dict, Optional
from unittest.mock import patch

import webtools_server.config as config
import webtools_server.security as security
import webtools_server.tools.fetch_url as fetch_url_module


class FakeStreamResponse:
    def __init__(
        self,
        status_code: int,
        headers: Optional[Dict[str, str]] = None,
        body: bytes = b"",
        encoding: Optional[str] = "utf-8",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.encoding = encoding

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        if self._body:
            yield self._body


class FakeStreamContext:
    def __init__(self, response: FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> FakeStreamResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeAsyncClient:
    def __init__(self, responses: list[FakeStreamResponse]) -> None:
        self._responses = responses
        self._index = 0

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, method: str, url: str, headers: Optional[Dict[str, str]] = None):
        if self._index >= len(self._responses):
            raise AssertionError("No more fake responses available")
        response = self._responses[self._index]
        self._index += 1
        return FakeStreamContext(response)


class TestFetchUrl(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_url_returns_error(self) -> None:
        result = await fetch_url_module.fetch_url("ftp://example.com")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "invalid_url")

    async def test_robots_blocked_returns_error(self) -> None:
        with patch("webtools_server.tools.fetch_url._validate_public_url"):
            with patch("webtools_server.tools.fetch_url._can_fetch", return_value=False):
                with patch(
                    "webtools_server.tools.fetch_url.httpx.AsyncClient",
                    return_value=FakeAsyncClient([]),
                ):
                    result = await fetch_url_module.fetch_url("http://example.com")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "blocked")
        self.assertEqual(result["error"]["blocked_reason"], "robots")

    async def test_content_length_too_large(self) -> None:
        headers = {
            "content-type": "text/html",
            "content-length": str(config.MAX_PAGE_BYTES + 1),
        }
        responses = [FakeStreamResponse(200, headers=headers)]
        with patch("webtools_server.tools.fetch_url._validate_public_url"):
            with patch("webtools_server.tools.fetch_url._can_fetch", return_value=True):
                with patch(
                    "webtools_server.tools.fetch_url.httpx.AsyncClient",
                    return_value=FakeAsyncClient(responses),
                ):
                    result = await fetch_url_module.fetch_url("http://example.com")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "too_large")
        self.assertEqual(result["error"]["content_length"], config.MAX_PAGE_BYTES + 1)

    async def test_unsupported_content_type(self) -> None:
        headers = {"content-type": "application/pdf"}
        responses = [FakeStreamResponse(200, headers=headers, body=b"%PDF-1.4")]
        with patch("webtools_server.tools.fetch_url._validate_public_url"):
            with patch("webtools_server.tools.fetch_url._can_fetch", return_value=True):
                with patch(
                    "webtools_server.tools.fetch_url.httpx.AsyncClient",
                    return_value=FakeAsyncClient(responses),
                ):
                    result = await fetch_url_module.fetch_url("http://example.com")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "unsupported_content_type")

    async def test_redirect_limit(self) -> None:
        headers = {"location": "http://example.com/next"}
        responses = [
            FakeStreamResponse(301, headers=headers)
            for _ in range(config.MAX_REDIRECTS + 1)
        ]
        with patch("webtools_server.tools.fetch_url._validate_public_url"):
            with patch("webtools_server.tools.fetch_url._can_fetch", return_value=True):
                with patch(
                    "webtools_server.tools.fetch_url.httpx.AsyncClient",
                    return_value=FakeAsyncClient(responses),
                ):
                    result = await fetch_url_module.fetch_url("http://example.com")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "redirect_limit")

    async def test_success_html_extracts_title_and_text(self) -> None:
        body = b"<html><head><title>Hi</title></head><body>Hello</body></html>"
        headers = {"content-type": "text/html"}
        responses = [FakeStreamResponse(200, headers=headers, body=body)]
        fake_trafilatura = types.SimpleNamespace(
            extract=lambda *_args, **_kwargs: "Hello"
        )
        with patch("webtools_server.tools.fetch_url._validate_public_url"):
            with patch("webtools_server.tools.fetch_url._can_fetch", return_value=True):
                with patch(
                    "webtools_server.tools.fetch_url.httpx.AsyncClient",
                    return_value=FakeAsyncClient(responses),
                ):
                    with patch.dict("sys.modules", {"trafilatura": fake_trafilatura}):
                        result = await fetch_url_module.fetch_url("http://example.com")
        self.assertNotIn("error", result)
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["title"], "Hi")
        self.assertEqual(result["extracted_text"], "Hello")
        self.assertEqual(result["bytes"], len(body))
        self.assertFalse(result["bytes_truncated"])


class TestValidationHelpers(unittest.TestCase):
    def test_validate_public_url_allows_public_host(self) -> None:
        with patch(
            "webtools_server.security._hostname_points_to_blocked_ip", return_value=False
        ):
            security._validate_public_url("http://example.com")

    def test_validate_public_url_blocks_localhost(self) -> None:
        with self.assertRaises(ValueError):
            security._validate_public_url("http://localhost")

    def test_validate_public_url_requires_hostname(self) -> None:
        with self.assertRaises(ValueError):
            security._validate_public_url("http:///no-host")

    def test_validate_public_url_blocks_metadata_ip(self) -> None:
        with self.assertRaises(ValueError):
            security._validate_public_url("http://169.254.169.254")

    def test_validate_public_url_blocks_resolved_private_ip(self) -> None:
        with patch(
            "webtools_server.security._hostname_points_to_blocked_ip", return_value=True
        ):
            with self.assertRaises(ValueError):
                security._validate_public_url("http://example.com")
