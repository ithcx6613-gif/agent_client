#!/usr/bin/env python3
"""One-time setup: register Entra app, create Azure Functions infra, deploy.

Prerequisites
-------------
- Azure CLI (``az``) logged in
- ``azd`` CLI installed
- Azure subscription with Contributor permissions

Steps
-----
1. Create/use an Azure Functions resource
2. Register a Microsoft Entra app for the MCP server
   (needed so Foundry's Agent Service knows which audience to request)
3. Build and deploy the function app
4. Configure Foundry project connection + toolbox
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def sh(cmd: str, capture: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {cmd}")
    kwargs = {"shell": True, "text": True}
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result = subprocess.run(cmd, **kwargs)
    if capture and result.returncode != 0:
        print(f"  ERROR: {result.stderr.strip()}")
    return result


def confirm(msg: str) -> bool:
    return input(f"\n  {msg} (y/n) [n]: ").strip().lower() == "y"


def main():
    print("=" * 60)
    print("MCP Server - Azure Functions Setup")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Load env / subscription info
    # ------------------------------------------------------------------
    print("\n[1/5] Checking Azure context...")
    sub_id = sh("az account show --query id -o tsv").stdout.strip()
    if not sub_id:
        print("  Not logged in. Run: az login")
        sys.exit(1)
    print(f"  Subscription: {sub_id}")

    tenant_id = sh("az account show --query tenantId -o tsv").stdout.strip()
    location = input("  Azure region [eastus2]: ").strip() or "eastus2"
    resource_group = input("  Resource group name [rg-mcp-server]: ").strip() or "rg-mcp-server"
    function_name = input("  Function app name (unique worldwide): ").strip()
    while not function_name:
        function_name = input("  Function app name: ").strip()
    storage_account = function_name.replace("-", "") + "store"

    # ------------------------------------------------------------------
    # 2. Create resource group
    # ------------------------------------------------------------------
    print(f"\n[2/5] Creating resource group {resource_group}...")
    sh(f"az group create --name {resource_group} --location {location}")

    # ------------------------------------------------------------------
    # 3. Create storage account + function app
    # ------------------------------------------------------------------
    print(f"\n[3/5] Creating function app {function_name}...")

    # Storage account
    result = sh(
        f"az storage account create --name {storage_account} "
        f"--resource-group {resource_group} --location {location} "
        f"--sku Standard_LRS --allow-blob-public-access false"
    )
    if result.returncode != 0:
        print("  Storage account creation failed (may already exist; continuing)")

    conn_string = sh(
        f"az storage account show-connection-string --name {storage_account} "
        f"--resource-group {resource_group} --query connectionString -o tsv"
    ).stdout.strip()

    # Function app (Linux, Python 3.12)
    result = sh(
        f"az functionapp create --name {function_name} "
        f"--resource-group {resource_group} "
        f"--storage-account {storage_account} "
        f"--consumption-plan-location {location} "
        f"--runtime python --runtime-version 3.12 "
        f"--functions-version 4 "
        f"--os-type Linux "
        f"--disable-app-insights false"
    )
    if result.returncode != 0:
        print("  Function app creation failed. Check the error above.")

    # Enable system-assigned managed identity
    sh(
        f"az functionapp identity assign --name {function_name} "
        f"--resource-group {resource_group}"
    )

    # ------------------------------------------------------------------
    # 4. Register Entra app for MCP server
    # ------------------------------------------------------------------
    print(f"\n[4/5] Registering Microsoft Entra app for the MCP server...")
    app_name = input(f"  Entra app display name [{function_name}-mcp]: ").strip() or f"{function_name}-mcp"

    result = sh(
        f"az ad app create --display-name '{app_name}' "
        f"--sign-in-audience AzureADMyOrg --query appId -o tsv"
    )
    server_app_id = result.stdout.strip()
    print(f"  Entra App ID: {server_app_id}")

    # Set the Application ID URI (the audience that Agent Service will use)
    app_uri = f"api://{server_app_id}"
    sh(f"az ad app update --id {server_app_id} --identifier-uris '{app_uri}'")

    # Create a client secret (for local testing / service principal)
    sh(
        f"az ad app credential reset --id {server_app_id} "
        f"--display-name 'mcp-server-secret' "
        f"--years 1"
    )

    # ------------------------------------------------------------------
    # 5. Configure function app environment variables
    # ------------------------------------------------------------------
    print(f"\n[5/5] Configuring environment variables for {function_name}...")
    sh(
        f"az functionapp config appsettings set --name {function_name} "
        f"--resource-group {resource_group} "
        f"--settings "
        f"TENANT_ID={tenant_id} "
        f"MCP_SERVER_CLIENT_ID={server_app_id} "
        f"MCP_AUTH_AUDIENCE={app_uri} "
        f"MCP_AUTH_ENABLED=true "
        f"FOUNDRY_ACCOUNT_NAME='' "
        f"FOUNDRY_PROJECT_NAME='' "
    )

    # ------------------------------------------------------------------
    # 6. Deploy the function app
    # ------------------------------------------------------------------
    print(f"\n[6/6] Deploying code to {function_name}...")
    deploy = confirm("Deploy now?")
    if deploy:
        os.chdir(REPO_ROOT)
        sh(f"func azure functionapp publish {function_name} --python")
        print(f"\n  ✅ Deployed! Function URL: https://{function_name}.azurewebsites.net")
    else:
        print("  Skipped. Deploy later with:")
        print(f"    cd {REPO_ROOT}")
        print(f"    func azure functionapp publish {function_name} --python")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Setup Complete!")
    print("=" * 60)
    print(f"\nEntra App ID (for Foundry connection): {server_app_id}")
    print(f"Application ID URI (audience): {app_uri}")
    print(f"Function app URL: https://{function_name}.azurewebsites.net")
    print(f"MCP SSE endpoint: https://{function_name}.azurewebsites.net/sse\n")
    print("Next steps in Foundry portal (https://ai.azure.com):")
    print("  1. Go to your project → Tool catalog")
    print("  2. Create a connection:")
    print("     - Type: Microsoft Entra")
    print("     - Auth: Agent identity")
    print("     - Audience: set to the Application ID URI above")
    print(f"     - Endpoint: https://{function_name}.azurewebsites.net/sse")
    print("  3. Create a Toolbox with an MCP tool:")
    print('     {')
    print('       "type": "mcp",')
    print(f'       "server_label": "micro_agent",')
    print(f'       "server_url": "https://{function_name}.azurewebsites.net/sse",')
    print('       "project_connection_id": "<connection-name-from-step-2>"')
    print('     }')
    print("  4. Attach the toolbox to your agent")
    print("\n  Then test with: 'hello_mcp' or 'save_snippet' prompt")


if __name__ == "__main__":
    main()
