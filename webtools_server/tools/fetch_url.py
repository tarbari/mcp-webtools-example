import re
import time
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import urljoin

import httpx

from webtools_server.app import mcp
from webtools_server.config import (
    FETCH_TIMEOUT,
    MAX_EXTRACT_CHARS,
    MAX_PAGE_BYTES,
    MAX_REDIRECTS,
    USER_AGENT,
)
from webtools_server.helpers import _error_response
from webtools_server.robots import _can_fetch
from webtools_server.security import _validate_public_url


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
