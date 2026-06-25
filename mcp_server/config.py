"""Configuration for the MCP Server, loaded from environment variables.

Uses a module-level singleton pattern so tools can import cfg directly
without needing to reference the server instance.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# --- Well-known Azure clouds ---
AZURE_PUBLIC = "login.microsoftonline.com"
AZURE_US_GOV = "login.microsoftonline.us"
AZURE_CHINA = "login.chinacloudapi.cn"


class McpServerConfig:
    """Configuration for MCP Server."""

    _instance = None

    def __new__(cls) -> "McpServerConfig":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        # Azure AD
        self.tenant_id = os.getenv("TENANT_ID", "")
        self.client_id = os.getenv("MCP_SERVER_CLIENT_ID", "")
        self.client_secret = os.getenv("MCP_SERVER_CLIENT_SECRET", "")

        # Microsoft Entra token validation (for Agent Identity auth)
        # The audience/api for token validation.
        # Should be the Application ID URI (e.g. "api://<app-id>") or App ID
        # of the MCP server's Entra app registration.
        self.auth_audience = os.getenv(
            "MCP_AUTH_AUDIENCE",
            os.getenv("MCP_SERVER_CLIENT_ID", ""),
        )
        # Azure AD authority hostname
        self.authority_host = os.getenv("MCP_AUTHORITY_HOST", AZURE_PUBLIC)
        # Set to "false" to disable token validation (local dev)
        self.auth_enabled = os.getenv("MCP_AUTH_ENABLED", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        # Server details
        self.host = os.getenv("MCP_SERVER_HOST", "0.0.0.0")
        self.port = int(os.getenv("MCP_SERVER_PORT", "8000"))
        self.transport = os.getenv("MCP_TRANSPORT", "sse")

        # Azure AI Foundry
        self.foundry_account_name = os.getenv("FOUNDRY_ACCOUNT_NAME", "")
        self.foundry_project_name = os.getenv("FOUNDRY_PROJECT_NAME", "")

        # Local in-memory storage for snippets
        self.snippets: dict[str, str] = {}

    @property
    def authority(self) -> str:
        return f"https://{self.authority_host}/{self.tenant_id}"

    @property
    def issuer(self) -> str:
        return f"{self.authority}/v2.0"


# Module-level singleton — tools import this directly
cfg = McpServerConfig()
