import os

import httpx

# ---- Network ----
SEARXNG_URL = "http://searxng:8080/search"
USER_AGENT = "mcp-webtools/0.1 (respect-robots)"
MAX_PAGE_BYTES = 2_500_000
MAX_EXTRACT_CHARS = 30_000
FETCH_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=10.0)
PDF_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
MAX_REDIRECTS = 5

# ---- Security ----
BLOCK_PRIVATE_NETS = True

# ---- Transport security ----
DEFAULT_ALLOWED_HOSTS = [
    "localhost:*",
    "127.0.0.1:*",
]

DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:*",
    "http://127.0.0.1:*",
]


def _split_env_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_allowed_entries(env_key: str) -> list[str]:
    value = os.getenv(env_key, "")
    return _split_env_list(value) if value else []


allowed_hosts = DEFAULT_ALLOWED_HOSTS + _load_allowed_entries("MCP_ALLOWED_HOSTS")
allowed_origins = DEFAULT_ALLOWED_ORIGINS + _load_allowed_entries("MCP_ALLOWED_ORIGINS")
