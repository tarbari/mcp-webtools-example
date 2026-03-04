# AGENTS

This file orients agentic coding tools to the conventions and workflows in this repository.

## Repository overview
- Root contains Docker and MCP config plus tests.
- Primary service lives in `webtools_server/`.
- Single Python entrypoint: `webtools_server/server.py`.
- Docker image name expected by compose: `webtools-mcp:latest`.
- Python deps are managed with uv at repo root.

## Build, run, lint, test

### Local setup (uv)
Run from repo root:

```sh
uv sync --extra dev
```

Run the server:

```sh
uv run webtools_server/server.py
```

### Docker build (recommended)
Run from repo root:

```sh
docker build -t webtools-mcp:latest .
```

### Docker compose
Run from repo root:

```sh
docker compose up -d
```

### Local run (without Docker)
From `webtools_server/`:

```sh
python server.py
```

### Lint
- No lint tool is configured in this repo.
- If you introduce one, document it here.

### Tests
- Use Python's built-in `unittest` runner (no extra dependency).
- If you add a different test runner (e.g., `pytest`), document it here.

Run all tests (from repo root):

```sh
uv run test
```

Run a single test module (from repo root):

```sh
uv run -m unittest tests.test_fetch_url
```

Run a single test case or method (from repo root):

```sh
uv run -m unittest tests.test_fetch_url.TestFetchUrl.test_fetch_html
```

Generate HTML test report:

```sh
uv run test-html
```

Generate HTML coverage report:

```sh
uv run coverage
```

### Requirements report
Keep `webtools_server/requirements.txt` as a generated report:

```sh
uv export --format requirements.txt --no-hashes --output-file webtools_server/requirements.txt
```

### Lockfile
`uv.lock` is the source of truth for resolved deps.

## Code style guidelines

### Language and runtime
- Target Python 3.12 for production (see Dockerfile).
- Local dev may use Python 3.13 if compatible with dependencies.
- Prefer asyncio-friendly patterns and `httpx.AsyncClient` for I/O.

### Imports
- Order groups: stdlib, third-party, local.
- Keep imports minimal and only include what a file uses.
- Avoid inline imports unless you want lazy loading or to prevent cycles.

### Formatting
- Follow PEP 8 with 4-space indentation.
- Keep lines reasonably short (~88 chars); avoid churn just for length.
- Use trailing commas in multiline literals for cleaner diffs.
- Prefer explicit dict/list formatting for response payloads.

### Types and signatures
- Use type hints for public tool functions and helpers.
- Prefer `Optional[T]`, `Dict[str, Any]`, `list[str]` over untyped dicts.
- Keep response schemas stable and explicit.
- When returning JSON, make it fully serializable (no custom classes).

### Naming conventions
- Functions: `snake_case`.
- Constants: `UPPER_SNAKE_CASE`.
- Classes: `PascalCase`.
- Tool names should be concise and action-oriented (e.g., `fetch_url`).

### Error handling
- Tool functions should return structured error payloads instead of raising.
- Use a shared helper to shape error responses consistently.
- Include `status_code`, `final_url`, and a stable `error.type` for callers.
- Log unexpected exceptions with context, but avoid leaking secrets.

### Networking and safety
- Validate URLs before requests and on every redirect hop.
- Enforce SSRF protections (block private IPs, loopback, link-local, etc.).
- Respect `robots.txt` (current policy is fail-open on fetch failures).
- Enforce download size limits and use streaming reads.

### Content handling
- Use content-type gating before running extraction.
- Normalize text outputs and cap extracted content length.
- Return metadata: content type, encoding, bytes read, truncation flags.

### FastMCP tool conventions
- Keep tool responses JSON-serializable.
- Use consistent top-level fields across success/error responses.
- Avoid side effects that aren’t clearly documented in the response.

## Project-specific notes
- Compose also starts `searxng` on localhost:8080.
- MCP config lives in `mcp.json` at repo root.

## Git commit style
- Use Conventional Commits for commit messages.
- Format: `type(scope): summary` or `type: summary`.
- Examples: `feat: add fetch_url robots cache`, `test: add web_search cases`.

## Test structure conventions
- Keep tests in `tests/` at repo root.
- Name files `test_*.py` and test classes `Test*`.
- Prefer one test module per feature or tool.
- Use `unittest.TestCase` and standard library assertions.

## Cursor / Copilot rules
- No `.cursor/rules/`, `.cursorrules`, or `.github/copilot-instructions.md` were found.
- If added later, summarize them here.

## Updating this file
- Keep this doc concise and accurate.
- Add new commands whenever tooling is introduced (lint, test, format, CI).
