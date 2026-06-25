#!/usr/bin/env bash
# ================================================================
# Setup Agent Identity Blueprint & Federated Credential
#
# This script creates:
#   1. A User-Assigned Managed Identity for the MCP server
#   2. A Federated Identity Credential on the MCP Server app
#      for OBO token exchange via managed identity
#   3. Outputs the AGENT_IDENTITY_BLUEPRINT_ID for your .env
#
# Prerequisites:
#   - Azure CLI (az) logged in
#   - Microsoft Graph permission: Application.ReadWrite.All
#   - MCP Server app registration already created
#
# Usage:
#   chmod +x scripts/setup_agent_identity.sh
#   ./scripts/setup_agent_identity.sh \
#       -t <tenant-id> \
#       -s <subscription-id> \
#       -g <resource-group> \
#       -a <mcp-server-app-client-id>
# ================================================================

set -euo pipefail

# --- Color helpers ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# --- Parse arguments ---
TENANT_ID=""
SUBSCRIPTION_ID=""
RESOURCE_GROUP=""
MCP_SERVER_CLIENT_ID=""
IDENTITY_NAME="micro-agent-mcp-identity"

while getopts "t:s:g:a:n:h" opt; do
  case $opt in
    t) TENANT_ID="$OPTARG" ;;
    s) SUBSCRIPTION_ID="$OPTARG" ;;
    g) RESOURCE_GROUP="$OPTARG" ;;
    a) MCP_SERVER_CLIENT_ID="$OPTARG" ;;
    n) IDENTITY_NAME="$OPTARG" ;;
    h)
      echo "Usage: $0 -t <tenant-id> -s <subscription-id> -g <resource-group> -a <mcp-server-app-client-id> [-n <identity-name>]"
      exit 0
      ;;
    *) error "Invalid option: -$OPTARG" ;;
  esac
done

if [[ -z "$TENANT_ID" || -z "$SUBSCRIPTION_ID" || -z "$RESOURCE_GROUP" || -z "$MCP_SERVER_CLIENT_ID" ]]; then
  error "Missing required arguments. Use -h for help."
fi

# --- Step 1: Create User-Assigned Managed Identity ---
info "Step 1: Creating User-Assigned Managed Identity: $IDENTITY_NAME ..."

MI_JSON=$(az identity create \
  --name "$IDENTITY_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --subscription "$SUBSCRIPTION_ID" \
  -o json 2>/dev/null) || {
  warn "Managed identity may already exist. Trying to retrieve it ..."
  MI_JSON=$(az identity show \
    --name "$IDENTITY_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --subscription "$SUBSCRIPTION_ID" \
    -o json) || error "Failed to create or retrieve managed identity."
}

MI_CLIENT_ID=$(echo "$MI_JSON" | jq -r '.clientId')
MI_PRINCIPAL_ID=$(echo "$MI_JSON" | jq -r '.principalId')

info "  Managed Identity created:"
info "    Name:        $IDENTITY_NAME"
info "    Client ID:   $MI_CLIENT_ID"
info "    Principal ID: $MI_PRINCIPAL_ID"

# --- Step 2: Create Federated Identity Credential ---
info ""
info "Step 2: Creating Federated Identity Credential on MCP Server App ..."

FIC_NAME="${IDENTITY_NAME}-fic"

az rest \
  --method POST \
  --uri "https://graph.microsoft.com/v1.0/applications(appId='${MCP_SERVER_CLIENT_ID}')/federatedIdentityCredentials" \
  --headers "Content-Type=application/json" \
  --body "$(cat <<EOF
{
  "name": "${FIC_NAME}",
  "issuer": "https://login.microsoftonline.com/${TENANT_ID}/v2.0",
  "subject": "${MI_PRINCIPAL_ID}",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "Federated identity credential for MCP Server managed identity"
}
EOF
)" || warn "Federated credential may already exist (this is usually fine)."

info "  Federated Identity Credential: ${FIC_NAME}"

# --- Step 3: Create Agent Identity Blueprint (Entra ID) ---
info ""
info "Step 3: Creating Agent Identity Blueprint in Entra ID ..."
info ""
info "  NOTE: Agent Identity Blueprints require the Microsoft Entra"
info "  SDK or portal. Run the Python script for full Entra setup:"
info ""
info "    python scripts/setup_agent_identity.py"
info ""

# --- Output ---
echo ""
echo "=============================================="
echo "  Setup Complete"
echo "=============================================="
echo ""
echo "Add these to your .env file:"
echo ""
echo "TENANT_ID=${TENANT_ID}"
echo "FOUNDRY_RESOURCE_GROUP=${RESOURCE_GROUP}"
echo ""
echo "# Managed Identity (for OBO token exchange)"
echo "MCP_SERVER_MI_CLIENT_ID=${MI_CLIENT_ID}"
echo ""
echo "# Agent Identity Blueprint (from Entra portal or SDK)"
echo "# AGENT_IDENTITY_BLUEPRINT_ID=<from-entra-portal>"
echo ""

info "Done!"
