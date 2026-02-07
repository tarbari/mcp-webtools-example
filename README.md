# Web Search Tool for AI

Build the `webtools-mcp` Docker image in `webtools_server/`, then start the stack:

```sh
docker build -t webtools-mcp:latest webtools_server
docker compose up -d
```

Ensure the image name matches `docker-compose.yaml`.

## Local setup (uv)

Sync the environment:

```sh
uv sync
```

Run the server:

```sh
uv run webtools_server/server.py
```

Run tests:

```sh
uv run test
```

Generate HTML test report:

```sh
uv run test-html
```

Generate HTML coverage report:

```sh
uv run coverage
```

Regenerate the requirements report (for Docker/ops workflows):

```sh
uv export --format requirements.txt --no-hashes --output-file webtools_server/requirements.txt
```

## Transport allowlist

Default allowed hosts and origins include localhost and 127.0.0.1. You can
append additional entries via environment variables (comma-separated):

```sh
MCP_ALLOWED_HOSTS="your.host:port"
MCP_ALLOWED_ORIGINS="http://your.host:port"
```

\* is a wild card for port.

## Notes

The current design favors simple image uploads to Portainer.
Consider adding a build stage to `docker-compose.yaml` if you want Compose to
build the image locally as part of `docker compose up`.

## AI usage disclaimer

This code is created with the help of AI. I have done my best to verify that it
works as intended, but I can not guarantee the tools safety.

Use at your own risk.
