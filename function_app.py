"""Azure Functions entry point for the MCP server.

Mounts the FastMCP SSE app (a Starlette ASGI application) into Azure Functions
using ``func.AsgiFunctionApp``, with Microsoft Entra token validation middleware.

Usage
-----
Local dev::

    func start

Deploy to Azure::

    func azure functionapp publish <FUNCTION_APP_NAME>
"""

import logging
import sys

import azure.functions as func

from mcp_server.auth.entra_auth import EntraAuthMiddleware
from mcp_server.config import cfg
from mcp_server.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("mcp-server.function")

# ---------------------------------------------------------------------------
# 1.  Create the FastMCP server (registers all tools)
# ---------------------------------------------------------------------------
mcp = create_app()

# ---------------------------------------------------------------------------
# 2.  Build the Starlette SSE app  (returns a fresh Starlette ASGI app)
# ---------------------------------------------------------------------------
sse_asgi = mcp.sse_app()

# ---------------------------------------------------------------------------
# 3.  Add Entra token-validation middleware and health endpoint
# ---------------------------------------------------------------------------
sse_asgi.add_middleware(EntraAuthMiddleware)


async def _health_check(request):
    """Simple health check endpoint for Azure Functions / load balancers."""
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok", "service": "mcp-server"})

sse_asgi.add_route("/health", _health_check, methods=["GET"])


# ---------------------------------------------------------------------------
# 4.  Wrap in Azure Functions ASGI function app
# ---------------------------------------------------------------------------
app = func.AsgiFunctionApp(
    app=sse_asgi,
    http_auth_level=func.AuthLevel.ANONYMOUS,
)

logger.info(
    "Azure Functions ASGI app ready — auth_enabled=%s, tenant=%s",
    cfg.auth_enabled,
    cfg.tenant_id,
)
