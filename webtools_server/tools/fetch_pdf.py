import time
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import urljoin

import httpx

from webtools_server.app import mcp
from webtools_server.config import (
    MAX_EXTRACT_CHARS,
    MAX_PAGE_BYTES,
    MAX_REDIRECTS,
    PDF_TIMEOUT,
    USER_AGENT,
)
from webtools_server.helpers import _error_response
from webtools_server.security import _validate_pdf_url


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
                        import io

                        import pypdf

                        pdf_reader = pypdf.PdfReader(io.BytesIO(bytes(raw)))
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
