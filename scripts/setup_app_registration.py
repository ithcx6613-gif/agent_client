#!/usr/bin/env python3
"""Azure AD App Registration setup for MCP Server + Agent Client.

This script creates or configures two app registrations in your tenant:
  1. Agent Client App — the Flask web app that users authenticate against
     (redirect URIs, required API permissions for AI Foundry + Graph).
  2. MCP Server App — the backend that receives OBO token exchanges
     (exposes scopes, pre-authorizes the client app, federated credential).

Usage:
  # Dry-run (just prints what would be created)
  python scripts/setup_app_registration.py --dry-run

  # Full setup (requires Azure CLI login + Contributor + Graph perms)
  python scripts/setup_app_registration.py \
      --tenant-id 9188040d-6c67-4c5b-b112-36a304b66dad \
      --redirect-uri http://localhost:5000/callback

Prerequisites:
  - Azure CLI (az) logged in:  az login
  - Microsoft Graph permission: Application.ReadWrite.All
"""

import argparse
import json
import os
import subprocess
import sys
import uuid


def run_az(*args: str) -> dict:
    """Run an Azure CLI command and return parsed JSON output."""
    result = subprocess.run(
        ["az"] + list(args),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"  [ERROR] az {' '.join(args)} failed:")
        print(f"    {result.stderr.strip()}")
        sys.exit(1)
    return json.loads(result.stdout)


def create_client_app(dry_run: bool, tenant_id: str, redirect_uri: str) -> str:
    """Create the Agent Client App Registration."""
    print(f"\n{'='*60}")
    print("STEP 1: Agent Client App Registration")
    print(f"{'='*60}")

    app_name = "micro-agent-client"

    if dry_run:
        print(f"  [DRY-RUN] Would create app: {app_name}")
        print(f"  [DRY-RUN] Redirect URI: {redirect_uri}")
        print(f"  [DRY-RUN] Required API access:")
        print(f"    - Azure AI Foundry (https://ai.azure.com/.default)")
        print(f"    - Microsoft Graph (User.Read)")
        return str(uuid.uuid4())

    # Create the app registration
    print(f"  Creating app registration: {app_name} ...")
    app = run_az(
        "ad", "app", "create",
        "--display-name", app_name,
        "--sign-in-audience", "AzureADMyOrg",
        "--web-redirect-uris", redirect_uri,
    )
    client_id = app["appId"]
    print(f"  Created: client_id={client_id}")

    # Add required API permissions
    print(f"  Adding API permissions ...")

    # Azure AI Foundry permission
    run_az(
        "ad", "app", "permission", "add",
        "--id", client_id,
        "--api", "https://ai.azure.com",
        "--api-permissions", "f8d9a9c6-1d1b-4f9c-8c4e-9f3b4c5d6e7f=Scope",
    )

    # Microsoft Graph User.Read
    run_az(
        "ad", "app", "permission", "add",
        "--id", client_id,
        "--api", "00000003-0000-0000-c000-000000000000",
        "--api-permissions", "e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope",
    )

    # Generate a client secret
    secret = run_az(
        "ad", "app", "credential", "reset",
        "--id", client_id,
        "--display-name", "mcp-server-secret",
        "--years", "1",
    )
    print(f"\n  Client credentials (save these securely!):")
    print(f"    AZURE_CLIENT_ID={client_id}")
    print(f"    AZURE_CLIENT_SECRET={secret['password']}")

    return client_id


def create_mcp_server_app(
    dry_run: bool, tenant_id: str, client_app_id: str,
) -> str:
    """Create the MCP Server App Registration for OBO token exchange."""
    print(f"\n{'='*60}")
    print("STEP 2: MCP Server App Registration (for OBO)")
    print(f"{'='*60}")

    app_name = "micro-agent-mcp-server"

    if dry_run:
        print(f"  [DRY-RUN] Would create app: {app_name}")
        print(f"  [DRY-RUN] Exposed scope: access_as_user")
        print(f"  [DRY-RUN] Pre-authorized client: {client_app_id}")
        return str(uuid.uuid4())

    print(f"  Creating app registration: {app_name} ...")
    app = run_az(
        "ad", "app", "create",
        "--display-name", app_name,
        "--sign-in-audience", "AzureADMyOrg",
    )
    server_client_id = app["appId"]
    print(f"  Created: client_id={server_client_id}")

    # Expose an OAuth2 scope for the MCP server
    scope_id = str(uuid.uuid4())
    app_id_uri = f"api://{server_client_id}"

    print(f"  Exposing OAuth2 scope: access_as_user ...")
    run_az(
        "ad", "app", "update",
        "--id", server_client_id,
        "--set",
        json.dumps({
            "identifierUris": [app_id_uri],
            "api": {
                "oauth2PermissionScopes": [{
                    "id": scope_id,
                    "value": "access_as_user",
                    "type": "User",
                    "isEnabled": True,
                    "userConsentDisplayName": "Access MCP Server as user",
                    "userConsentDescription": (
                        "Allow the app to call MCP tools on your behalf"
                    ),
                    "adminConsentDisplayName": "Access MCP Server as user",
                    "adminConsentDescription": (
                        "Allow the app to call MCP tools on your behalf"
                    ),
                }],
                "preAuthorizedApplications": [{
                    "appId": client_app_id,
                    "delegatedPermissionIds": [scope_id],
                }],
                "requestedAccessTokenVersion": 2,
            },
        }),
    )

    print(f"\n  MCP Server App created:")
    print(f"    MCP_SERVER_CLIENT_ID={server_client_id}")
    print(f"    Identifier URI: {app_id_uri}")
    print(f"    Scope: {app_id_uri}/access_as_user")

    return server_client_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up Azure AD App Registrations for the Agent + MCP Server."
    )
    parser.add_argument("--tenant-id", help="Azure AD tenant ID")
    parser.add_argument(
        "--redirect-uri",
        default="http://localhost:5000/callback",
        help="OAuth redirect URI for the agent client",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be created without making changes.",
    )
    args = parser.parse_args()

    print("Azure AD App Registration Setup for micro-agent")
    print("=" * 60)
    print()

    if args.dry_run:
        print("  DRY-RUN MODE — no changes will be made.\n")

    tenant_id = args.tenant_id or os.getenv("TENANT_ID", "")
    if not tenant_id:
        print(
            "ERROR: --tenant-id is required or set TENANT_ID env var.\n"
            "Hint: az account show --query tenantId -o tsv"
        )
        sys.exit(1)

    client_app_id = create_client_app(args.dry_run, tenant_id, args.redirect_uri)
    server_app_id = create_mcp_server_app(
        args.dry_run, tenant_id, client_app_id,
    )

    print(f"\n{'='*60}")
    print("Summary")
    print(f"{'='*60}")
    print(f"\nAdd these to your .env file:\n")
    print(f"TENANT_ID={tenant_id}")
    print(f"CLIENT_ID={client_app_id}")
    print(f"# CLIENT_SECRET=<generated above>")
    print(f"REDIRECT_URI={args.redirect_uri}")
    print(f"MCP_SERVER_CLIENT_ID={server_app_id}")
    print(f"SCOPE=https://ai.azure.com/.default")
    print()
    print("Grant admin consent for the API permissions:")
    print(f"  az ad app permission admin-consent --id {client_app_id}")


if __name__ == "__main__":
    main()
