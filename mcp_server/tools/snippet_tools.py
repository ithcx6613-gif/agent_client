"""Snippet management tools — CRUD operations for code/text snippets.

Demonstrates tools with structured input/output, similar to the
remote-mcp-functions-python FunctionsMcpTool reference.
"""

import json
import logging

from mcp_server.config import cfg

logger = logging.getLogger("mcp-server.snippets")


def register_tools(mcp) -> None:
    """Register snippet tools on the given FastMCP instance."""

    @mcp.tool()
    def save_snippet(name: str, content: str) -> str:
        """Save a code or text snippet with a given name.

        Args:
            name: The name of the snippet (used as identifier).
            content: The snippet content.

        Returns:
            Confirmation message.
        """
        cfg.snippets[name] = content
        logger.info("Saved snippet: %s (%d chars)", name, len(content))
        return f"Snippet '{name}' saved successfully ({len(content)} chars)."

    @mcp.tool()
    def get_snippet(name: str) -> str:
        """Retrieve a saved snippet by name.

        Args:
            name: The name of the snippet to retrieve.

        Returns:
            The snippet content or an error message.
        """
        content = cfg.snippets.get(name)
        if content is None:
            return f"Snippet '{name}' not found."
        logger.info("Retrieved snippet: %s", name)
        return content

    @mcp.tool()
    def list_snippets() -> str:
        """List all saved snippet names.

        Returns:
            A formatted list of snippet names and their sizes.
        """
        if not cfg.snippets:
            return "No snippets saved."

        lines = [
            f"  - {name} ({len(content)} chars)"
            for name, content in sorted(cfg.snippets.items())
        ]
        return f"Saved snippets ({len(lines)}):\n" + "\n".join(lines)

    @mcp.tool()
    def delete_snippet(name: str) -> str:
        """Delete a saved snippet by name.

        Args:
            name: The name of the snippet to delete.

        Returns:
            Confirmation or error message.
        """
        if name not in cfg.snippets:
            return f"Snippet '{name}' not found."
        del cfg.snippets[name]
        logger.info("Deleted snippet: %s", name)
        return f"Snippet '{name}' deleted."

    @mcp.tool()
    def get_snippet_with_metadata(name: str) -> str:
        """Retrieve a snippet with metadata (name, size, timestamp info).

        Args:
            name: The name of the snippet.

        Returns:
            JSON string with content and metadata.
        """
        content = cfg.snippets.get(name)
        metadata = {
            "name": name,
            "found": content is not None,
            "character_count": len(content) if content else 0,
        }
        if content:
            metadata["content"] = content
        return json.dumps(metadata, indent=2, ensure_ascii=False)
