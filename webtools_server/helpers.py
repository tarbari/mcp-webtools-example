from typing import Any, Dict, Optional


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
