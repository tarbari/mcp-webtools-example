import types
import unittest
from typing import AsyncIterator, Dict, Optional
from unittest.mock import patch

import webtools_server.server as server


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


class TestFetchPdf(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_url_returns_error(self) -> None:
        result = await server.fetch_pdf("ftp://example.com/file.pdf")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "invalid_url")

    async def test_blocked_hostname_returns_error(self) -> None:
        with patch(
            "webtools_server.server._validate_pdf_url",
            side_effect=ValueError("Blocked hostname"),
        ):
            result = await server.fetch_pdf("http://localhost/file.pdf")
        self.assertIn("error", result)
        # When _validate_pdf_url fails, it returns invalid_url error
        self.assertEqual(result["error"]["type"], "invalid_url")
        self.assertEqual(result["error"]["message"], "Blocked hostname")

    async def test_unsupported_content_type(self) -> None:
        headers = {"content-type": "text/html"}
        responses = [FakeStreamResponse(200, headers=headers)]
        with patch("webtools_server.server._validate_pdf_url"):
            with patch(
                "webtools_server.server.httpx.AsyncClient",
                return_value=FakeAsyncClient(responses),
            ):
                result = await server.fetch_pdf("http://example.com/file.pdf")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "unsupported_content_type")

    async def test_pdf_password_protected(self) -> None:
        headers = {"content-type": "application/pdf"}
        
        class FakePdfReader:
            def __init__(self, data):
                raise Exception("PDF is encrypted")

        fake_pypdf = types.SimpleNamespace(
            PdfReader=FakePdfReader,
            errors=types.SimpleNamespace(PdfReadError=Exception),
        )
        responses = [FakeStreamResponse(200, headers=headers, body=b"%PDF-1.4")]
        
        with patch("webtools_server.server._validate_pdf_url"):
            with patch(
                "webtools_server.server.httpx.AsyncClient",
                return_value=FakeAsyncClient(responses),
            ):
                with patch.dict("sys.modules", {"pypdf": fake_pypdf}):
                    result = await server.fetch_pdf("http://example.com/file.pdf")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "pdf_password_protected")

    async def test_pdf_parse_error(self) -> None:
        headers = {"content-type": "application/pdf"}
        
        class FakePdfReader:
            def __init__(self, data):
                raise Exception("Invalid PDF format")

        fake_pypdf = types.SimpleNamespace(
            PdfReader=FakePdfReader,
            errors=types.SimpleNamespace(PdfReadError=Exception),
        )
        responses = [FakeStreamResponse(200, headers=headers, body=b"not a pdf")]
        
        with patch("webtools_server.server._validate_pdf_url"):
            with patch(
                "webtools_server.server.httpx.AsyncClient",
                return_value=FakeAsyncClient(responses),
            ):
                with patch.dict("sys.modules", {"pypdf": fake_pypdf}):
                    result = await server.fetch_pdf("http://example.com/file.pdf")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "pdf_parse_error")

    async def test_success_pdf_extracts_text(self) -> None:
        headers = {"content-type": "application/pdf"}
        
        class FakePage:
            def extract_text(self):
                return "Extracted text from page 1"

        class FakePdfReader:
            def __init__(self, data):
                self.pages = [FakePage()]
                self.metadata = None

        fake_pypdf = types.SimpleNamespace(
            PdfReader=FakePdfReader,
            errors=types.SimpleNamespace(PdfReadError=Exception),
        )
        
        body = b"%PDF-1.4 test content"
        responses = [FakeStreamResponse(200, headers=headers, body=body)]
        
        with patch("webtools_server.server._validate_pdf_url"):
            with patch(
                "webtools_server.server.httpx.AsyncClient",
                return_value=FakeAsyncClient(responses),
            ):
                with patch.dict("sys.modules", {"pypdf": fake_pypdf}):
                    result = await server.fetch_pdf("http://example.com/file.pdf")
        self.assertNotIn("error", result)
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["content_type"], "application/pdf")
        self.assertEqual(result["extracted_text"], "Extracted text from page 1")
        self.assertEqual(result["pages"], 1)
        self.assertFalse(result["text_truncated"])
        self.assertEqual(result["bytes"], len(body))

    async def test_success_pdf_with_metadata(self) -> None:
        headers = {"content-type": "application/pdf"}
        
        class FakePage:
            def extract_text(self):
                return "Extracted text"

        class FakeMetadata:
            def items(self):
                return [("/Title", "Test PDF"), ("/Author", "John Doe")]

        class FakePdfReader:
            def __init__(self, data):
                self.pages = [FakePage()]
                self.metadata = FakeMetadata()

        fake_pypdf = types.SimpleNamespace(
            PdfReader=FakePdfReader,
            errors=types.SimpleNamespace(PdfReadError=Exception),
        )
        
        body = b"%PDF-1.4 test content"
        responses = [FakeStreamResponse(200, headers=headers, body=body)]
        
        with patch("webtools_server.server._validate_pdf_url"):
            with patch(
                "webtools_server.server.httpx.AsyncClient",
                return_value=FakeAsyncClient(responses),
            ):
                with patch.dict("sys.modules", {"pypdf": fake_pypdf}):
                    result = await server.fetch_pdf("http://example.com/file.pdf")
        self.assertNotIn("error", result)
        self.assertEqual(result["metadata"]["title"], "Test PDF")
        self.assertEqual(result["metadata"]["author"], "John Doe")

    async def test_redirect_follows_location(self) -> None:
        headers1 = {"location": "http://example.com/file2.pdf"}
        headers2 = {"content-type": "application/pdf"}
        
        class FakePage:
            def extract_text(self):
                return "Text from final PDF"

        class FakePdfReader:
            def __init__(self, data):
                self.pages = [FakePage()]
                self.metadata = None

        fake_pypdf = types.SimpleNamespace(
            PdfReader=FakePdfReader,
            errors=types.SimpleNamespace(PdfReadError=Exception),
        )
        
        responses = [
            FakeStreamResponse(301, headers=headers1),
            FakeStreamResponse(200, headers=headers2, body=b"%PDF-1.4"),
        ]
        
        with patch("webtools_server.server._validate_pdf_url"):
            with patch(
                "webtools_server.server.httpx.AsyncClient",
                return_value=FakeAsyncClient(responses),
            ):
                with patch.dict("sys.modules", {"pypdf": fake_pypdf}):
                    result = await server.fetch_pdf("http://example.com/file.pdf")
        self.assertNotIn("error", result)
        self.assertEqual(result["final_url"], "http://example.com/file2.pdf")

    async def test_redirect_blocked_returns_error(self) -> None:
        headers1 = {"location": "http://localhost/redirected"}
        
        responses = [FakeStreamResponse(301, headers=headers1)]
        
        with patch("webtools_server.server._validate_pdf_url") as mock_validate:
            mock_validate.side_effect = lambda url: (
                _ for _ in ()
            ).throw(ValueError("Blocked hostname")) if "localhost" in url else None
            
            with patch(
                "webtools_server.server.httpx.AsyncClient",
                return_value=FakeAsyncClient(responses),
            ):
                result = await server.fetch_pdf("http://example.com/file.pdf")
        self.assertIn("error", result)
        self.assertEqual(result["error"]["type"], "blocked")


class TestValidationHelpers(unittest.TestCase):
    def test_validate_pdf_url_allows_public_host(self) -> None:
        with patch(
            "webtools_server.server._hostname_points_to_blocked_ip", return_value=False
        ):
            server._validate_pdf_url("http://example.com/file.pdf")

    def test_validate_pdf_url_blocks_localhost(self) -> None:
        with self.assertRaises(ValueError):
            server._validate_pdf_url("http://localhost/file.pdf")

    def test_validate_pdf_url_requires_hostname(self) -> None:
        with self.assertRaises(ValueError):
            server._validate_pdf_url("http:///no-host/file.pdf")


if __name__ == "__main__":
    unittest.main()
