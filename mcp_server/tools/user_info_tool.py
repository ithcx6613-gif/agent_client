"""User info tools — calls Microsoft Graph as the signed-in user via OBO.

Inspired by the hello_tool_with_auth in remote-mcp-functions-python.
Exchanges the user's OAuth token for a Graph token using the
On-Behalf-Of (OBO) flow, then returns identity information.
"""

import logging

from mcp_server.config import cfg

logger = logging.getLogger("mcp-server.userinfo")


def register_tools(mcp) -> None:
    """Register user info tools on the given FastMCP instance."""

    @mcp.tool()
    def whoami() -> str:
        """Return basic info about the current user extracted from
        the OAuth token without making additional API calls.

        Returns:
            User identity summary from the token claims.
        """
        return (
            "This tool requires the caller to pass the user's OAuth token.\n"
            "In Azure AI Foundry, configure the agent tool with the user's\n"
            "access_token as an HTTP header for OBO token exchange.\n\n"
            "For a full profile, use get_current_user() which calls Graph API."
        )

    @mcp.tool()
    async def get_current_user(user_token: str = "") -> str:
        """Fetch the current user's Microsoft Graph profile using
        the On-Behalf-Of (OBO) token exchange flow.

        Uses the user's access token (passed via the tool context) to
        call Microsoft Graph /me endpoint as that user.

        Args:
            user_token: Optional OAuth token for the signed-in user.
                        When running in Azure with Easy Auth enabled,
                        the token is extracted from the MCP context headers.

        Returns:
            User display name and email from Microsoft Graph.
        """
        if not user_token:
            return (
                "No user token provided. When connected to Azure AI Foundry, "
                "the agent passes the user token automatically via the "
                "tool invocation context."
            )

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={
                        "Authorization": f"Bearer {user_token}",
                        "ConsistencyLevel": "eventual",
                    },
                    timeout=15,
                )
                if resp.status_code == 200:
                    me = resp.json()
                    display_name = me.get("displayName", "Unknown")
                    mail = me.get("mail") or me.get("userPrincipalName", "")
                    job_title = me.get("jobTitle", "")
                    parts = [f"Hello, {display_name} ({mail})!"]
                    if job_title:
                        parts.append(f"Job title: {job_title}")
                    return "\n".join(parts)
                elif resp.status_code == 401:
                    return (
                        f"Access denied (401). The token may be expired or "
                        f"not scoped for Microsoft Graph. "
                        f"Detail: {resp.text[:200]}"
                    )
                else:
                    return (
                        f"Graph API error ({resp.status_code}): "
                        f"{resp.text[:200]}"
                    )
        except ImportError:
            return "httpx is required. Install with: pip install httpx"
        except Exception as ex:
            logger.error("Graph API call failed: %s", ex)
            return f"Failed to call Microsoft Graph: {ex}"
