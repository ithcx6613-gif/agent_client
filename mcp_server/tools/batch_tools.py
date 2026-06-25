"""Batch operation tools — demonstrate array inputs and bulk processing.

Inspired by the batch_save_snippets tool from remote-mcp-functions-python.
"""

import json
import logging

from mcp_server.config import cfg

logger = logging.getLogger("mcp-server.batch")


def register_tools(mcp) -> None:
    """Register batch operation tools on the given FastMCP instance."""

    @mcp.tool()
    def batch_save_snippets(items: str) -> str:
        """Save multiple snippets in a single tool invocation.

        Accepts a JSON string representing an array of snippet objects,
        each with 'name' and 'content' keys.

        Args:
            items: JSON string. Example:
                   [{"name":"example1","content":"code here"},
                    {"name":"example2","content":"more code"}]

        Returns:
            JSON summary of saved snippets.
        """
        try:
            snippet_list = json.loads(items)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid JSON: {e}"})

        if not isinstance(snippet_list, list):
            return json.dumps({"error": "Expected a JSON array"})

        saved = []
        for item in snippet_list:
            name = item.get("name")
            content = item.get("content", "")
            if not name:
                continue
            cfg.snippets[name] = content
            saved.append(name)
            logger.info("Batch saved snippet: %s", name)

        result = {
            "message": f"Successfully saved {len(saved)} snippets",
            "snippets": saved,
        }
        return json.dumps(result, indent=2, ensure_ascii=False)

    @mcp.tool()
    def batch_get_snippets(names: str) -> str:
        """Retrieve multiple snippets by name in a single call.

        Args:
            names: JSON array of snippet names. Example: ["snippet1","snippet2"]

        Returns:
            JSON object mapping names to their content (or null if not found).
        """
        try:
            name_list = json.loads(names)
        except json.JSONDecodeError as e:
            return json.dumps({"error": f"Invalid JSON: {e}"})

        if not isinstance(name_list, list):
            return json.dumps({"error": "Expected a JSON array"})

        result = {}
        for name in name_list:
            result[name] = cfg.snippets.get(name)

        return json.dumps(result, indent=2, ensure_ascii=False)
