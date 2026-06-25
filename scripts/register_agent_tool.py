#!/usr/bin/env python3
"""Register the MCP Server as a tool on an existing Azure AI Foundry Agent.

This script uses the Azure AI Foundry SDK to add an MCP tool definition
to an existing agent, enabling the agent to call the tools exposed by the
MCP Server during conversations.

Usage:
  python scripts/register_agent_tool.py \
      --mcp-endpoint http://localhost:8000 \
      --mcp-scope https://mcp-server-app.azurewebsites.net/access_as_user

Prerequisites:
  - python-dotenv, azure-ai-projects installed
  - .env file with FOUNDRY_ACCOUNT_NAME, FOUNDRY_PROJECT_NAME, AGENT_NAME
  - Authenticated Azure CLI session (az login)
"""

import argparse
import json
import os
import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPToolDefinition
from azure.identity import AzureCliCredential

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register MCP Server as a tool on an AI Foundry Agent."
    )
    parser.add_argument(
        "--mcp-endpoint",
        required=True,
        help="URL of the MCP server (e.g., http://localhost:8000)",
    )
    parser.add_argument(
        "--mcp-scope",
        default="",
        help="OAuth scope for the MCP server (for remote auth)",
    )
    parser.add_argument(
        "--agent-name",
        default=os.getenv("AGENT_NAME", ""),
        help="Name of the agent to update",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the tool definition without registering.",
    )
    args = parser.parse_args()

    account_name = os.getenv("FOUNDRY_ACCOUNT_NAME", "")
    project_name = os.getenv("FOUNDRY_PROJECT_NAME", "")
    agent_name = args.agent_name

    if not all([account_name, project_name, agent_name]):
        print("ERROR: Set FOUNDRY_ACCOUNT_NAME, FOUNDRY_PROJECT_NAME, and AGENT_NAME")
        print("       in your .env file or pass --agent-name explicitly.")
        sys.exit(1)

    endpoint = (
        f"https://{account_name}.services.ai.azure.com"
        f"/api/projects/{project_name}"
    )

    # Build the MCP tool definition
    tool_def = MCPToolDefinition(
        name="micro-agent-tools",
        description="MCP tools including snippet management, user info, and batch operations",
        endpoint=args.mcp_endpoint,
    )

    if args.mcp_scope:
        tool_def.auth = {"authorization": {"scope": args.mcp_scope}}

    if args.dry_run:
        print("DRY RUN — Tool definition that would be registered:\n")
        print(json.dumps(tool_def.as_dict(), indent=2, ensure_ascii=False))
        return

    # Connect to AI Foundry and update the agent
    print(f"Connecting to AI Foundry: {endpoint}")
    credential = AzureCliCredential()

    client = AIProjectClient(
        endpoint=endpoint,
        credential=credential,
        api_version="v1",
        allow_preview=True,
    )

    print(f"Updating agent '{agent_name}' with MCP tool definition ...")
    agent = client.agents.update_agent(
        agent_name=agent_name,
        tool_set=[tool_def],
    )

    print(f"Agent updated successfully!")
    print(f"  Agent ID: {agent.id}")
    print(f"  MCP Endpoint: {args.mcp_endpoint}")
    print(f"  Tools registered: micro-agent-tools")


if __name__ == "__main__":
    main()
