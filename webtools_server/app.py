import contextlib

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from webtools_server.config import allowed_hosts, allowed_origins

mcp = FastMCP(
    "Web Tools",
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    ),
)

# Import tools package — auto-discovery registers all tools via @mcp.tool()
import webtools_server.tools  # noqa: E402, F401


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    # Required for Streamable HTTP session management
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Mount("/", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)
