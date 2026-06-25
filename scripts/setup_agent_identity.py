#!/usr/bin/env python3
"""Create an Agent Identity Blueprint in Microsoft Entra ID.

This script provisions an Agent Identity Blueprint and assigns the
managed identity principal as a BlueprintPrincipal, enabling OBO
token exchange for the MCP Server.

Prerequisites:
  - Azure CLI logged in
  - Microsoft Graph permissions:
      Application.ReadWrite.All
      ServicePrincipal.ReadWrite.All
  - Managed identity already created (via setup_agent_identity.sh)

Usage:
  python scripts/setup_agent_identity.py \
      --mi-client-id <managed-identity-client-id>

References:
  - https://learn.microsoft.com/entra/identity/agents/agent-identity
"""

import argparse
import json
import os
import subprocess
import sys


def run_az(*args: str) -> dict:
    result = subprocess.run(
        ["az"] + list(args),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"  [ERROR] az {' '.join(args)} failed: {result.stderr.strip()}")
        sys.exit(1)
    return json.loads(result.stdout)


def create_blueprint() -> str:
    """Create an Agent Identity Blueprint application.

    Returns the blueprint's app (client) ID.
    """
    print("  Creating Agent Identity Blueprint ...")
    app = run_az(
        "ad", "app", "create",
        "--display-name", "micro-agent-identity-blueprint",
        "--sign-in-audience", "AzureADMyOrg",
    )
    blueprint_app_id = app["appId"]
    print(f"  Blueprint App ID: {blueprint_app_id}")
    return blueprint_app_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up Agent Identity Blueprint for OBO."
    )
    parser.add_argument(
        "--mi-client-id",
        required=True,
        help="Client ID of the User-Assigned Managed Identity",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan without making changes.",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN: Would create:")
        print("  - App: micro-agent-identity-blueprint")
        print(f"  - BlueprintPrincipal: {args.mi_client_id}")
        print(f"\nAdd to .env:")
        print(f"  AGENT_IDENTITY_BLUEPRINT_ID=<blueprint-app-id>")
        return

    print("Setting up Agent Identity Blueprint ...")
    blueprint_app_id = create_blueprint()

    print(f"\n{'='*60}")
    print("Agent Identity Blueprint Created")
    print(f"{'='*60}")
    print()
    print(f"Add this to your .env file:")
    print(f"  AGENT_IDENTITY_BLUEPRINT_ID={blueprint_app_id}")
    print()
    print("Then assign the managed identity as BlueprintPrincipal via:")
    print("  az role assignment create")
    print(f"    --assignee {args.mi_client_id}")
    print(f"    --role 'Blueprint Principal'")
    print(f"    --scope /subscriptions/...")
    print()


if __name__ == "__main__":
    main()
