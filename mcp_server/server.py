"""FastMCP server entry point.

Registers all tool blueprints and starts the server with SSE transport,
making it compatible with the Azure AI Foundry agent tool integration.
"""

import logging
import sys

from mcp.server.fastmcp import FastMCP

from mcp_server.config import cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp-server")


def create_app() -> FastMCP:
    """Create and configure the FastMCP server, registering all tools."""
    mcp = FastMCP(
        name="ai-foundry-agent-tools",
        instructions=(
            "MCP tools for Azure AI Foundry Agent. "
            "Provides snippet management, user identity lookup, "
            "and utility tools callable via OAuth."
        ),
        host=cfg.host,
        port=cfg.port,
    )

    # ------------------------------------------------------------------
    # Register tool modules
    # ------------------------------------------------------------------
    from mcp_server.tools import hello_tool
    from mcp_server.tools import snippet_tools
    from mcp_server.tools import user_info_tool
    from mcp_server.tools import batch_tools

    hello_tool.register_tools(mcp)
    snippet_tools.register_tools(mcp)
    user_info_tool.register_tools(mcp)
    batch_tools.register_tools(mcp)

    logger.info(
        "MCP Server initialized. Transport=%s, Host=%s:%s",
        cfg.transport,
        cfg.host,
        cfg.port,
    )
    return mcp


def main() -> None:
    """Run the MCP server."""
    if cfg.transport == "sse":
        logger.info("Starting SSE transport on %s:%s", cfg.host, cfg.port)
        create_app().run(transport="sse")
    else:
        logger.info("Starting stdio transport")
        create_app().run(transport="stdio")


if __name__ == "__main__":
    main()
