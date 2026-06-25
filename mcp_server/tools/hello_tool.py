"""Hello world MCP tool — simple identity & version tool."""

import logging

logger = logging.getLogger("mcp-server.hello")


def register_tools(mcp) -> None:
    """Register hello/identity tools on the given FastMCP instance."""

    @mcp.tool()
    def hello_mcp() -> str:
        """Greet the user and confirm the MCP server is running."""
        logger.info("hello_mcp called")
        return (
            "Hello! I am the MCP Tool Server for Azure AI Foundry Agent.\n"
            "I can help with snippet management, user identity lookup, "
            "and other tasks."
        )

    @mcp.tool()
    def server_info() -> str:
        """Return the current server version and capability summary."""
        info = [
            "MCP Server v0.1.0",
            "Transport: SSE",
            "Available tools:",
            "  - hello_mcp           - Greeting",
            "  - server_info         - Server info",
            "  - save_snippet        - Save a code snippet",
            "  - get_snippet         - Retrieve a snippet",
            "  - list_snippets       - List all snippets",
            "  - whoami              - Current user info (via OBO)",
            "  - get_current_user    - Get user profile from Graph (via OBO)",
            "  - batch_save_snippets - Save multiple snippets at once",
        ]
        return "\n".join(info)
