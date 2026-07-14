#!/usr/bin/env bash
# ================================================================
# init_new_account.sh — 一键 Azure 环境初始化脚本（账号迁移用）
#
# 说明
# ----
# 该脚本根据当前代码仓（micro-agent）的架构，在新 Azure 账号中
# 创建并配置全部所需的 Azure 资源，包含以下四个部分：
#
#   1. Entra ID App Registration（两个：Agent Client + MCP Server）
#   2. Azure AI Foundry Hub + Project + Agent
#   3. Container App（agent_client 前端 + 后端）
#   4. Function App（MCP Server）
#
# 用法
# ----
#   bash scripts/init_new_account.sh                      # 全自动引导
#   bash scripts/init_new_account.sh --dry-run            # 仅展示，不创建
#   bash scripts/init_new_account.sh --env-name prod      # 指定环境名
#
# 前置条件
# --------
#   - Azure CLI (az) 已安装并登录
#   - Azure Developer CLI (azd) 已安装
#   - Azure Functions Core Tools (func) 已安装
#   - Python 3.12+
#   - 拥有以下权限：
#     * "Application.ReadWrite.All"（Graph 权限，创建 App Registration）
#     * "Contributor" 或 "Owner"（创建 Azure 资源）
#     * AI Foundry 创建权限（可在门户中手动完成）
# ================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# 颜色 / 日志
# ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
banner(){ echo -e "${CYAN}$*${NC}"; }
step()  { echo; echo -e "${CYAN}══════════════════════════════════════════${NC}"; echo -e "${CYAN}  $*${NC}"; echo -e "${CYAN}══════════════════════════════════════════${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ─────────────────────────────────────────────────────────────────
# 默认值
# ─────────────────────────────────────────────────────────────────
DRY_RUN=false
ENV_NAME="dev"
LOCATION="eastus2"
SKIP_FOUNDRY=false
SKIP_CONTAINER=false
SKIP_FUNCTION=false
SKIP_ENTRA=false
SKIP_VAULT=false
OBO_ENABLED=false
AGENT_CLIENT_REDIRECT_URI="http://localhost:5000/callback"

# ─────────────────────────────────────────────────────────────────
# 参数解析
# ─────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)            DRY_RUN=true; shift ;;
    --env-name)           ENV_NAME="$2"; shift 2 ;;
    --location)           LOCATION="$2"; shift 2 ;;
    --skip-foundry)       SKIP_FOUNDRY=true; shift ;;
    --skip-container-app) SKIP_CONTAINER=true; shift ;;
    --skip-function-app)  SKIP_FUNCTION=true; shift ;;
    --skip-entra)         SKIP_ENTRA=true; shift ;;
    --skip-vault)         SKIP_VAULT=true; shift ;;
    --enable-obo)         OBO_ENABLED=true; shift ;;
    --redirect-uri)       AGENT_CLIENT_REDIRECT_URI="$2"; shift 2 ;;
    -h|--help)
      echo "用法: $0 [选项]"
      echo ""
      echo "选项:"
      echo "  --dry-run               展示计划，不创建资源"
      echo "  --env-name NAME         环境名称（默认: dev）"
      echo "  --location REGION       Azure 区域（默认: eastus2）"
      echo "  --skip-foundry         跳过 AI Foundry 创建"
      echo "  --skip-container-app    跳过 Container App 部署"
      echo "  --skip-function-app     跳过 Function App 部署"
      echo "  --skip-entra           跳过 App Registration 创建"
      echo "  --enable-obo           启用 OBO（On-Behalf-Of）模式"
      echo "  --redirect-uri URI      Agent Client OAuth 回调"
      echo "  -h, --help             显示帮助"
      exit 0
      ;;
    *) error "未知选项: $1（使用 -h 查看帮助）" ;;
  esac
done

DRY_PREFIX=""
$DRY_RUN && DRY_PREFIX="[DRY-RUN] "

run_az() {
  if $DRY_RUN; then echo "  ${DRY_PREFIX}az $*"; return 0; fi
  echo "  \$ az $*"
  az "$@" || error "az 命令失败: $*"
}

run_azd() {
  if $DRY_RUN; then echo "  ${DRY_PREFIX}azd $*"; return 0; fi
  echo "  \$ azd $*"
  azd "$@" || error "azd 命令失败: $*"
}

confirm_step() {
  local prompt="$1"
  if $DRY_RUN; then return 0; fi
  echo ""
  read -r -p "  ❓ $prompt (y/N) " response
  case "$response" in [yY]|[yY][eE][sS]) return 0 ;; *) echo "  ⏭️  跳过"; return 1 ;; esac
}

# macOS 兼容的 sed
local_sed() {
  local key="$1" val="$2" file="$3"
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s|^${key}=.*|${key}=${val}|" "$file" 2>/dev/null || true
  else
    sed -i "s|^${key}=.*|${key}=${val}|" "$file" 2>/dev/null || true
  fi
}

# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
step "Phase 0: 前置检查"

if $DRY_RUN; then
  info "运行在 DRY-RUN 模式 — 只展示计划，不创建任何资源"
fi

# 工具链检查
MISSING_TOOLS=()
for cmd in az azd func jq python3 pwsh; do
  if command -v "$cmd" &>/dev/null; then
    info "  ✅ $cmd 已安装"
  else
    MISSING_TOOLS+=("$cmd")
  fi
done
[[ ${#MISSING_TOOLS[@]} -gt 0 ]] && error "缺少必需工具: ${MISSING_TOOLS[*]}"

# Azure 登录状态
AZ_ACCOUNT=$(az account show 2>/dev/null || true)
[[ -z "$AZ_ACCOUNT" ]] && error "未登录 Azure。请先运行: az login"

AZ_TENANT_ID=$(echo "$AZ_ACCOUNT" | jq -r '.tenantId')
AZ_USER=$(echo "$AZ_ACCOUNT" | jq -r '.user.name')
AZ_SUBSCRIPTION_ID=$(echo "$AZ_ACCOUNT" | jq -r '.id')
AZ_SUBSCRIPTION_NAME=$(echo "$AZ_ACCOUNT" | jq -r '.name')

info "  租户:     $AZ_TENANT_ID"
info "  用户:     $AZ_USER"
info "  订阅:     $AZ_SUBSCRIPTION_NAME ($AZ_SUBSCRIPTION_ID)"

if [[ "$AZ_SUBSCRIPTION_ID" == "null" ]]; then
  warn "未选择订阅，列出可用订阅："
  az account list --output table
  read -r -p "  输入要使用的订阅 ID: " AZ_SUBSCRIPTION_ID
  run_az account set --subscription "$AZ_SUBSCRIPTION_ID"
fi

info "  环境:     $ENV_NAME"
info "  区域:     $LOCATION"

# ═══════════════════════════════════════════════════════════════════
# Phase 1: Entra App Registrations
# ═══════════════════════════════════════════════════════════════════
AGENT_CLIENT_ID=""; AGENT_CLIENT_SECRET=""
MCP_SERVER_CLIENT_ID=""; MCP_SERVER_CLIENT_SECRET=""
ENTRA_APP_OBJECT_ID=""
ENTRA_BACKEND_CLIENT_ID=""

if ! $SKIP_ENTRA; then
  step "Phase 1: Microsoft Entra App Registrations"

  # ── 1a: Agent Client SPA App ──────────────────────────────────
  info "1a. 创建 Agent Client SPA App..."
  AGENT_CLIENT_APP_NAME="micro-agent-client-${ENV_NAME}"

  if ! $DRY_RUN; then
    EXISTING=$(az ad app list --display-name "$AGENT_CLIENT_APP_NAME" --query "[0].appId" -o tsv 2>/dev/null || true)
    if [[ -n "$EXISTING" ]] && confirm_step "Agent Client App 已存在 (${EXISTING})，复用?"; then
      AGENT_CLIENT_ID="$EXISTING"
    else
      AGENT_CLIENT_ID=$(az ad app create \
        --display-name "$AGENT_CLIENT_APP_NAME" \
        --sign-in-audience "AzureADMyOrg" \
        --web-redirect-uris "$AGENT_CLIENT_REDIRECT_URI" \
        --query "appId" -o tsv)
    fi

    # 记录 Object ID（用于后续手动操作）
    ENTRA_APP_OBJECT_ID=$(az ad app show --id "$AGENT_CLIENT_ID" --query id -o tsv)

    # 添加 API 权限: Azure AI Foundry (https://ai.azure.com/.default)
    info "  添加 API 权限..."
    az ad app permission add \
      --id "$AGENT_CLIENT_ID" \
      --api "https://ai.azure.com" \
      --api-permissions "1a7925b5-f871-417a-9b8b-303f9f29fa10=Scope" 2>/dev/null || \
      warn "  AI Foundry 权限添加可能失败（需 admin 同意），可在门户手动添加"

    # Microsoft Graph User.Read
    az ad app permission add \
      --id "$AGENT_CLIENT_ID" \
      --api "00000003-0000-0000-c000-000000000000" \
      --api-permissions "e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope" 2>/dev/null || \
      warn "  Graph User.Read 权限添加失败，可在门户手动添加"

    # 生成 Client Secret
    AGENT_CLIENT_SECRET=$(az ad app credential reset \
      --id "$AGENT_CLIENT_ID" \
      --display-name "client-secret-${ENV_NAME}" \
      --years "1" \
      --query "password" -o tsv 2>/dev/null || echo "")
    if [[ -z "$AGENT_CLIENT_SECRET" ]]; then
      warn "  Client Secret 生成失败（只读账号），请在 Azure 门户手动创建"
      AGENT_CLIENT_SECRET="<需在门户创建>"
    fi

    # 配置 requiredResourceAccess（API 权限正式注册）
    info "  配置 requiredResourceAccess（API 权限声明）..."
    AGENT_CLIENT_OBJ_ID=$(az ad app show --id "$AGENT_CLIENT_ID" --query id -o tsv 2>/dev/null || echo "")
    if [[ -n "$AGENT_CLIENT_OBJ_ID" ]]; then
      # Azure AI Foundry (https://ai.azure.com/.default) + Microsoft Graph User.Read
      RESOURCE_ACCESS_BODY=$(cat <<RAEOF
{
  "requiredResourceAccess": [
    {
      "resourceAppId": "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe",
      "resourceAccess": [
        {"id": "1a7925b5-f871-417a-9b8b-303f9f29fa10", "type": "Scope"}
      ]
    },
    {
      "resourceAppId": "00000003-0000-0000-c000-000000000000",
      "resourceAccess": [
        {"id": "e1fe6dd8-ba31-4d61-89e7-88639da4683d", "type": "Scope"}
      ]
    }
  ]
}
RAEOF
)
      az rest --method PATCH         --uri "https://graph.microsoft.com/v1.0/applications/${AGENT_CLIENT_OBJ_ID}"         --headers "Content-Type=application/json"         --body "$RESOURCE_ACCESS_BODY" 2>/dev/null ||         warn "  requiredResourceAccess 设置失败，可在门户手动配置"
      info "  ✅ requiredResourceAccess 已配置"
    fi
  else
    AGENT_CLIENT_ID="<待创建: $AGENT_CLIENT_APP_NAME>"
  fi

  # ── 1b: MCP Server App Registration ───────────────────────────
  echo ""
  info "1b. 创建 MCP Server App..."
  MCP_SERVER_APP_NAME="micro-agent-mcp-server-${ENV_NAME}"

  if ! $DRY_RUN; then
    EXISTING=$(az ad app list --display-name "$MCP_SERVER_APP_NAME" --query "[0].appId" -o tsv 2>/dev/null || true)
    if [[ -n "$EXISTING" ]] && confirm_step "MCP Server App 已存在 (${EXISTING})，复用?"; then
      MCP_SERVER_CLIENT_ID="$EXISTING"
    else
      MCP_SERVER_CLIENT_ID=$(az ad app create \
        --display-name "$MCP_SERVER_APP_NAME" \
        --sign-in-audience "AzureADMyOrg" \
        --query "appId" -o tsv)

      # 设置 identifier URI
      az ad app update \
        --id "$MCP_SERVER_CLIENT_ID" \
        --identifier-uris "api://${MCP_SERVER_CLIENT_ID}"

      # 暴露 access_as_user scope
      SCOPE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
      az ad app update \
        --id "$MCP_SERVER_CLIENT_ID" \
        --set "api.oauth2PermissionScopes=[{\"id\":\"${SCOPE_ID}\",\"value\":\"access_as_user\",\"type\":\"User\",\"isEnabled\":true,\"userConsentDisplayName\":\"Access MCP Server as user\",\"userConsentDescription\":\"Allows the app to access MCP Server on your behalf\"}]"
    fi

      # Pre-authorize Agent Client SPA（允许 SPA 免用户同意调用 MCP Server API）
      if [[ -n "${AGENT_CLIENT_ID:-}" && "$AGENT_CLIENT_ID" != "<"* ]]; then
        info "  配置 preAuthorizedApplications..."
        MCP_SERVER_OBJ_ID=$(az ad app show --id "$MCP_SERVER_CLIENT_ID" --query id -o tsv 2>/dev/null || echo "")
        if [[ -n "$MCP_SERVER_OBJ_ID" ]]; then
          PREAUTH_BODY=$(cat <<PAEOF
{
  "api": {
    "oauth2PermissionScopes": [{"id":"${SCOPE_ID}","value":"access_as_user","type":"User","isEnabled":true,"userConsentDisplayName":"Access MCP Server as user","userConsentDescription":"Allows the app to access MCP Server on your behalf"}],
    "preAuthorizedApplications": [
      {
        "appId": "${AGENT_CLIENT_ID}",
        "permissionIds": ["${SCOPE_ID}"]
      }
    ]
  }
}
PAEOF
)
          az rest --method PATCH             --uri "https://graph.microsoft.com/v1.0/applications/${MCP_SERVER_OBJ_ID}"             --headers "Content-Type=application/json"             --body "$PREAUTH_BODY" 2>/dev/null ||             warn "  preAuthorizedApplications 设置失败，可在门户手动配置"
          info "  ✅ Agent Client 已预授权调用 MCP Server API"
        fi
      fi


    # 生成 Secret
    MCP_SERVER_CLIENT_SECRET=$(az ad app credential reset \
      --id "$MCP_SERVER_CLIENT_ID" \
      --display-name "mcp-server-secret-${ENV_NAME}" \
      --years "1" \
      --query "password" -o tsv 2>/dev/null || echo "")
    if [[ -z "$MCP_SERVER_CLIENT_SECRET" ]]; then
      warn "  MCP Server Secret 生成失败，请在 Azure 门户手动创建"
      MCP_SERVER_CLIENT_SECRET="<需在门户创建>"
    fi

    # 配置 MCP Server 的 requiredResourceAccess（Azure ML，用于 OBO 场景）
    info "  配置 MCP Server requiredResourceAccess..."
    MCP_SERVER_OBJ_ID="${MCP_SERVER_OBJ_ID:-$(az ad app show --id "$MCP_SERVER_CLIENT_ID" --query id -o tsv 2>/dev/null || echo "")}"
    if [[ -n "$MCP_SERVER_OBJ_ID" ]]; then
      MCP_RESOURCE_ACCESS_BODY=$(cat <<MRAEOF
{
  "requiredResourceAccess": [
    {
      "resourceAppId": "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe",
      "resourceAccess": [
        {"id": "1a7925b5-f871-417a-9b8b-303f9f29fa10", "type": "Scope"}
      ]
    }
  ]
}
MRAEOF
)
      az rest --method PATCH         --uri "https://graph.microsoft.com/v1.0/applications/${MCP_SERVER_OBJ_ID}"         --headers "Content-Type=application/json"         --body "$MCP_RESOURCE_ACCESS_BODY" 2>/dev/null ||         warn "  MCP Server requiredResourceAccess 设置失败，可在门户手动配置"
      info "  ✅ MCP Server requiredResourceAccess 已配置"
    fi
  else
    MCP_SERVER_CLIENT_ID="<待创建: $MCP_SERVER_APP_NAME>"
  fi

  echo ""
  info "  ✅ Entra App Registrations 就绪"
  info "    Agent Client ID:     $AGENT_CLIENT_ID"
  info "    MCP Server ID:       $MCP_SERVER_CLIENT_ID"

  # ── 1c: Admin Consent — 授予租户级授权 ────────────────────────
  echo ""
  info "1c. 授予租户级 Admin Consent..."
  echo ""
  info "  Agent Client App 需要以下 Delegated Permission 的管理员同意："
  info "    - Azure AI Foundry (https://ai.azure.com): user_impersonation"
  info "    - Microsoft Graph: User.Read"
  echo ""

  if ! $DRY_RUN && confirm_step "立即授予 Admin Consent（需拥有 Global Admin / Privileged Role Admin 权限）?"; then
    # Azure Machine Learning Services SP
    AML_SP_ID=$(az ad sp show --id "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe" --query id -o tsv 2>/dev/null || echo "")
    # Microsoft Graph SP
    GRAPH_SP_ID=$(az ad sp show --id "00000003-0000-0000-c000-000000000000" --query id -o tsv 2>/dev/null || echo "")
    # Agent Client SP
    CLIENT_SP_ID=$(az ad sp show --id "$AGENT_CLIENT_ID" --query id -o tsv 2>/dev/null || echo "")

    if [[ -n "$AML_SP_ID" && -n "$CLIENT_SP_ID" ]]; then
      info "  授予 Azure AI Foundry user_impersonation..."
      az rest --method POST         --uri "https://graph.microsoft.com/v1.0/oauth2PermissionGrants"         --headers "Content-Type=application/json"         --body "{\"clientId\":\"${CLIENT_SP_ID}\",\"consentType\":\"AllPrincipals\",\"resourceId\":\"${AML_SP_ID}\",\"scope\":\"user_impersonation\"}" 2>/dev/null ||         warn "  Azure ML 授权失败（可能已存在），可在门户手动操作"
    else
      warn "  无法找到 Azure Machine Learning 服务主体，跳过自动授权"
    fi

    if [[ -n "$GRAPH_SP_ID" && -n "$CLIENT_SP_ID" ]]; then
      info "  授予 Microsoft Graph User.Read..."
      az rest --method POST         --uri "https://graph.microsoft.com/v1.0/oauth2PermissionGrants"         --headers "Content-Type=application/json"         --body "{\"clientId\":\"${CLIENT_SP_ID}\",\"consentType\":\"AllPrincipals\",\"resourceId\":\"${GRAPH_SP_ID}\",\"scope\":\"User.Read\"}" 2>/dev/null ||         warn "  Graph 授权失败（可能已存在），可在门户手动操作"
    fi

    info "  ✅ Admin Consent 已完成"
  fi

  if $DRY_RUN; then
    info "[DRY-RUN] Phase 1 计划:"
    echo "    创建 Agent Client SPA App - ${AGENT_CLIENT_APP_NAME}"
    echo "      - Redirect URI: ${AGENT_CLIENT_REDIRECT_URI}"
    echo "      - API: AI Foundry + Graph User.Read"
    echo "      - requiredResourceAccess 注册"
    echo "    创建 MCP Server App - ${MCP_SERVER_APP_NAME}"
    echo "      - Expose: access_as_user scope"
    echo "      - Pre-authorize Agent Client"
    echo "      - requiredResourceAccess: Azure ML"
    echo "    Admin Consent: 两个 API 的租户级授权"
  fi

fi

# ═══════════════════════════════════════════════════════════════════
# Phase 2: AI Foundry Hub + Project + Agent
# ═══════════════════════════════════════════════════════════════════
AI_RG=""; AI_HUB_NAME=""; AI_PROJECT_NAME=""; AI_AGENT_ID=""
AI_PROJECT_ENDPOINT=""

if ! $SKIP_FOUNDRY; then
  step "Phase 2: Azure AI Foundry"

  AI_RG="rg-ai-foundry-${ENV_NAME}"
  AI_HUB_NAME="hub-${ENV_NAME}"
  AI_PROJECT_NAME="proj-${ENV_NAME}"
  AI_AGENT_NAME="${ENV_NAME}-agent"

  info "创建 AI Services 资源（AI Foundry Hub 的基础）..."
  if ! $DRY_RUN; then
    # 检查是否已有 AI Services
    EXISTING_HUB=$(az cognitiveservices account show \
      --name "$AI_HUB_NAME" --resource-group "$AI_RG" 2>/dev/null || true)
    if [[ -n "$EXISTING_HUB" ]]; then
      info "  AI Services 资源已存在: $AI_HUB_NAME"
    else
      # 创建资源组
      az group create --name "$AI_RG" --location "$LOCATION"
      # 创建 AI Services
      az cognitiveservices account create \
        --name "$AI_HUB_NAME" \
        --resource-group "$AI_RG" \
        --location "$LOCATION" \
        --kind "AIServices" \
        --sku "S0" \
        --yes
      info "  ✅ AI Services 创建完成"
    fi

    # 获取 Endpoint
    AI_HUB_ENDPOINT=$(az cognitiveservices account show \
      --name "$AI_HUB_NAME" --resource-group "$AI_RG" \
      --query "properties.endpoint" -o tsv 2>/dev/null || echo "")
    info "  AI Services Endpoint: ${AI_HUB_ENDPOINT:-<未知>}"
  fi

  # 交互式: AI Foundry Project 和 Agent 必须在门户中创建
  echo ""
  info "⚠️  AI Foundry Project 和 Agent 需要在 https://ai.azure.com 门户中手动创建"
  echo ""
  info "  请完成以下步骤："
  info "    1. 打开 https://ai.azure.com"
  info "    2. 进入 Hub → Projects → + New Project"
  info "       - 项目名称: ${AI_PROJECT_NAME}"
  info "       - Hub: ${AI_HUB_NAME}"
  info "    3. 记录 Project Endpoint（格式如 https://{hub}.services.ai.azure.com/api/projects/{proj}）"
  info "    4. 进入项目 → Agents → + Create"
  info "       - 名称: ${AI_AGENT_NAME}"
  info "       - 模型: gpt-4o-mini（或你选择的模型）"
  info "       - 记录 Agent ID"
  echo ""
  if ! $DRY_RUN; then
    read -r -p "  输入 Project Endpoint（输入后回车，留空跳过）: " AI_PROJECT_ENDPOINT_INPUT
    AI_PROJECT_ENDPOINT="${AI_PROJECT_ENDPOINT_INPUT:-}"
    read -r -p "  输入 Agent 名称/ID（输入后回车，留空跳过）: " AI_AGENT_ID_INPUT
    AI_AGENT_ID="${AI_AGENT_ID_INPUT:-}"

    # 自动构造 endpoint（如果只给了 hub name）
    if [[ -z "$AI_PROJECT_ENDPOINT" && -n "$AI_HUB_NAME" && -n "$AI_PROJECT_NAME" ]]; then
      AI_PROJECT_ENDPOINT="https://${AI_HUB_NAME}.services.ai.azure.com/api/projects/${AI_PROJECT_NAME}"
      info "  自动构造 Project Endpoint: $AI_PROJECT_ENDPOINT"
    fi
  fi

  echo ""
  info "  ✅ AI Foundry 配置摘要"
  info "    Hub:              ${AI_HUB_NAME}"
  info "    Project:          ${AI_PROJECT_NAME:-<待创建>}"
  info "    Project Endpoint: ${AI_PROJECT_ENDPOINT:-<待填写>}"
  info "    Agent:            ${AI_AGENT_ID:-<待创建>}"
  info "    资源组:           ${AI_RG}"
fi

# ═══════════════════════════════════════════════════════════════════
# Phase 3: Container App (agent_client)
# ═══════════════════════════════════════════════════════════════════
CONTAINER_APP_URL=""
CONTAINER_APP_NAME=""
CONTAINER_RG=""

if ! $SKIP_CONTAINER; then
  step "Phase 3: Container App — agent_client 部署"

  AGENT_CLIENT_DIR="$REPO_ROOT/agent_client"
  if [[ ! -d "$AGENT_CLIENT_DIR" ]]; then
    warn "  agent_client 目录不存在，跳过 Container App 部署"
    SKIP_CONTAINER=true
  else
    info "使用 azd 部署 Container App..."
    $DRY_RUN && info "[DRY-RUN] 以下为 azd 部署计划："

    if ! $DRY_RUN; then
      cd "$AGENT_CLIENT_DIR"

      # 初始化 azd 环境
      EXISTING_ENV=$(azd env list 2>/dev/null | grep "$ENV_NAME" || true)
      if [[ -z "$EXISTING_ENV" ]]; then
        run_azd env new "$ENV_NAME" --location "$LOCATION"
      else
        run_azd env select "$ENV_NAME"
      fi

      # 设置环境变量
      run_azd env set AZURE_ENV_NAME "$ENV_NAME"
      run_azd env set AZURE_LOCATION "$LOCATION"
      run_azd env set ENTRA_TENANT_ID "$AZ_TENANT_ID"
      [[ -n "${AI_PROJECT_ENDPOINT:-}" ]] && run_azd env set AI_AGENT_ENDPOINT "$AI_PROJECT_ENDPOINT"
      [[ -n "${AI_AGENT_ID:-}" ]] && run_azd env set AI_AGENT_ID "$AI_AGENT_ID"
      run_azd env set ENABLE_OBO "$( $OBO_ENABLED && echo "true" || echo "false" )"

      # 执行部署
      if confirm_step "运行 azd up 进行 Container App 完整部署（约 10-15 分钟）?"; then
        info "开始部署 Container App..."
        run_azd up
        info "  ✅ Container App 部署完成"

        CONTAINER_APP_URL=$(azd env get-value WEB_ENDPOINT 2>/dev/null || echo "")
        CONTAINER_APP_NAME=$(azd env get-value AZURE_CONTAINER_APP_NAME 2>/dev/null || echo "")
        CONTAINER_RG=$(azd env get-value AZURE_RESOURCE_GROUP_NAME 2>/dev/null || echo "")

        info "  Container App URL: ${CONTAINER_APP_URL:-<获取失败>}"
        info "  Container App 名称: ${CONTAINER_APP_NAME:-<获取失败>}"

        # 部署后更新 Entra App 的 Redirect URI
        if [[ -n "$AGENT_CLIENT_ID" && "$AGENT_CLIENT_ID" != "<"* && -n "$CONTAINER_APP_URL" ]]; then
          info "  更新 Entra App Redirect URI..."
          az ad app update \
            --id "$AGENT_CLIENT_ID" \
            --web-redirect-uris "$AGENT_CLIENT_REDIRECT_URI" "https://${CONTAINER_APP_URL}/callback" "https://${CONTAINER_APP_URL}/auth/callback" 2>/dev/null || \
            warn "  Redirect URI 更新失败，可在门户手动添加：https://${CONTAINER_APP_URL}/callback"
        fi
      else
        info "跳过 azd up。稍后可手动运行:"
        info "    cd $AGENT_CLIENT_DIR && azd up"
      fi

      cd - >/dev/null
    else
      # DRY-RUN 展示
      echo "    cd $AGENT_CLIENT_DIR"
      echo "    azd env new $ENV_NAME --location $LOCATION"
      echo "    azd env set ENTRA_TENANT_ID $AZ_TENANT_ID"
      echo "    azd env set AI_AGENT_ENDPOINT ${AI_PROJECT_ENDPOINT:-<待填写>}"
      echo "    azd env set AI_AGENT_ID ${AI_AGENT_ID:-<待填写>}"
      echo "    azd env set ENABLE_OBO $( $OBO_ENABLED && echo "true" || echo "false" )"
      echo "    azd up"
    fi
  fi
fi

# ═══════════════════════════════════════════════════════════════════
# Phase 4: Function App (MCP Server)
# ═══════════════════════════════════════════════════════════════════
MCP_FUNC_NAME=""; MCP_FUNC_URL=""; MCP_RG=""

if ! $SKIP_FUNCTION; then
  step "Phase 4: Function App — MCP Server 部署"

  MCP_RG="rg-mcp-server-${ENV_NAME}"
  MCP_FUNC_NAME="func-mcp-${ENV_NAME}"
  MCP_STORAGE="mcpstore${ENV_NAME}$(echo $RANDOM | md5sum 2>/dev/null | head -c 6 || echo $RANDOM)"
  MCP_UMI_NAME="umi-mcp-${ENV_NAME}"

  # 检查 Python 版本
  PYTHON_RUNTIME="3.12"

  info "创建资源组 ${MCP_RG}..."
  run_az group create --name "$MCP_RG" --location "$LOCATION"

  # 创建 Storage Account
  info "创建 Storage Account ${MCP_STORAGE}..."
  run_az storage account create \
    --name "$MCP_STORAGE" \
    --resource-group "$MCP_RG" \
    --location "$LOCATION" \
    --sku "Standard_LRS" \
    --allow-blob-public-access false

  # 创建 Function App（消耗计划）
  info "创建 Function App ${MCP_FUNC_NAME}..."
  run_az functionapp create \
    --name "$MCP_FUNC_NAME" \
    --resource-group "$MCP_RG" \
    --storage-account "$MCP_STORAGE" \
    --consumption-plan-location "$LOCATION" \
    --runtime "python" \
    --runtime-version "$PYTHON_RUNTIME" \
    --functions-version "4" \
    --os-type "Linux"

  # 启用系统托管标识
  info "启用系统托管标识..."
  MCP_MI_PRINCIPAL_ID=$(run_az functionapp identity assign \
    --name "$MCP_FUNC_NAME" \
    --resource-group "$MCP_RG" \
    --query "principalId" -o tsv)

  # 配置环境变量
  info "配置 Function App 环境变量..."
  run_az functionapp config appsettings set \
    --name "$MCP_FUNC_NAME" \
    --resource-group "$MCP_RG" \
    --settings \
      "TENANT_ID=${AZ_TENANT_ID}" \
      "MCP_SERVER_CLIENT_ID=${MCP_SERVER_CLIENT_ID}" \
      "MCP_AUTH_AUDIENCE=api://${MCP_SERVER_CLIENT_ID}" \
      "MCP_AUTH_ENABLED=true" \
      "FOUNDRY_ACCOUNT_NAME=${AI_HUB_NAME:-}" \
      "FOUNDRY_PROJECT_NAME=${AI_PROJECT_NAME:-}" \
      "MCP_SERVER_HOST=0.0.0.0" \
      "MCP_SERVER_PORT=8000" \
      "MCP_TRANSPORT=sse"

  # 可选：创建 User-Assigned Managed Identity 用于 OBO
  if $OBO_ENABLED; then
    info "创建 User-Assigned Managed Identity..."
    MCP_UMI_JSON=$(run_az identity create \
      --name "$MCP_UMI_NAME" \
      --resource-group "$MCP_RG" -o json 2>/dev/null || echo '{}')
    MCP_UMI_CLIENT_ID=$(echo "$MCP_UMI_JSON" | jq -r '.clientId // empty')
    MCP_UMI_PRINCIPAL_ID=$(echo "$MCP_UMI_JSON" | jq -r '.principalId // empty')

    if [[ -n "$MCP_UMI_CLIENT_ID" && -n "$MCP_SERVER_CLIENT_ID" && "$MCP_SERVER_CLIENT_ID" != "<"* ]]; then
      info "创建 Federated Identity Credential..."
      FIC_NAME="umi-mcp-fic-${ENV_NAME}"
      FIC_BODY=$(cat <<EOF
{
  "name": "${FIC_NAME}",
  "issuer": "https://login.microsoftonline.com/${AZ_TENANT_ID}/v2.0",
  "subject": "${MCP_UMI_PRINCIPAL_ID}",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "Federated identity credential for MCP Server managed identity"
}
EOF
      )
                              ficAppId="${MCP_SERVER_CLIENT_ID}"
      MCP_APP_OBJ_ID=$(az ad app show --id "$MCP_SERVER_CLIENT_ID" --query id -o tsv 2>/dev/null || echo "")
      if [[ -n "$MCP_APP_OBJ_ID" ]]; then
        run_az rest --method POST \
          --uri "https://graph.microsoft.com/v1.0/applications/${MCP_APP_OBJ_ID}/federatedIdentityCredentials" \
          --headers "Content-Type=application/json" \
          --body "$FIC_BODY" 2>/dev/null || \
          warn "  FIC 创建失败（可能已存在），可在门户手动创建"
      fi
    fi
  fi

  # 计算部署后的 URL
  MCP_FUNC_URL="https://${MCP_FUNC_NAME}.azurewebsites.net"

  # 部署代码
  echo ""
  info "部署 MCP Server 代码..."
  if ! $DRY_RUN && confirm_step "立即发布 MCP Server 代码到 Function App?"; then
    cd "$REPO_ROOT"

    info "安装 Python 依赖..."
    run_az functionapp config set \
      --name "$MCP_FUNC_NAME" \
      --resource-group "$MCP_RG" \
      --linux-fx-version "PYTHON|${PYTHON_RUNTIME}"

    info "发布代码..."
    # 尝试 func azure functionapp publish
    if command -v func &>/dev/null; then
      func azure functionapp publish "$MCP_FUNC_NAME" --python 2>/dev/null || {
        warn "func publish 失败，使用 zip 部署..."
        # 回退到 zip 部署
        python3 -c "
import tempfile, os, zipfile
tmp = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk('.'):
        # 排除不需要的目录
        dirs[:] = [d for d in dirs if not d.startswith(('.', '__')) and d not in ('node_modules', '.venv', '__pycache__')]
        for f in files:
            fn = os.path.join(root, f)
            zf.write(fn, fn)
tmp.close()
print(tmp.name)
" | while read -r ZIP_FILE; do
          az functionapp deployment source config-zip \
            --name "$MCP_FUNC_NAME" \
            --resource-group "$MCP_RG" \
            --src "$ZIP_FILE" || warn "  zip 部署失败"
          rm -f "$ZIP_FILE"
        done
      }
    else
      warn "  func 命令不可用，请手动运行："
      warn "    func azure functionapp publish $MCP_FUNC_NAME --python"
    fi

    cd - >/dev/null
    info "  ✅ MCP Server 部署完成"
  else
    info "跳过部署。稍后可手动运行："
    info "    cd $REPO_ROOT"
    info "    func azure functionapp publish $MCP_FUNC_NAME --python"
  fi

  info "  ✅ MCP Server 配置就绪"
  info "    Function App:    ${MCP_FUNC_NAME}"
  info "    SSE Endpoint:    ${MCP_FUNC_URL}/sse"
  info "    资源组:          ${MCP_RG}"
fi

# ═══════════════════════════════════════════════════════════════════
# Phase 5: 注册 MCP Tool 到 AI Foundry Agent
# ═══════════════════════════════════════════════════════════════════
step "Phase 5: 注册 MCP Tool 到 AI Agent"

MCP_SCOPE=""
if [[ -n "$MCP_SERVER_CLIENT_ID" && "$MCP_SERVER_CLIENT_ID" != "<"* ]]; then
  MCP_SCOPE="api://${MCP_SERVER_CLIENT_ID}/access_as_user"
fi

if [[ -n "${AI_AGENT_ID:-}" && "${AI_AGENT_ID}" != "<"* ]]; then
  echo ""
  info "执行 MCP Tool 注册..."
  echo ""
  info "命令："
  echo "    python $SCRIPT_DIR/register_agent_tool.py \\"
  echo "        --mcp-endpoint ${MCP_FUNC_URL:-http://localhost:8000}/sse \\"
  [[ -n "$MCP_SCOPE" ]] && echo "        --mcp-scope $MCP_SCOPE \\"
  echo "        --agent-name ${AI_AGENT_ID}"
  echo ""

  if ! $DRY_RUN && confirm_step "立即注册 MCP Tool 到 AI Agent?"; then
    REG_SCRIPT="$SCRIPT_DIR/register_agent_tool.py"
    if [[ -f "$REG_SCRIPT" ]]; then
      MCP_ENDPOINT="${MCP_FUNC_URL:-http://localhost:8000}/sse"
      if [[ -n "$MCP_SCOPE" ]]; then
        python3 "$REG_SCRIPT" \
          --mcp-endpoint "$MCP_ENDPOINT" \
          --mcp-scope "$MCP_SCOPE" \
          --agent-name "$AI_AGENT_ID" || warn "  MCP 工具注册失败（可能 agent 尚未就绪或脚本不存在）"
      else
        python3 "$REG_SCRIPT" \
          --mcp-endpoint "$MCP_ENDPOINT" \
          --agent-name "$AI_AGENT_ID" || warn "  MCP 工具注册失败"
      fi
    else
      warn "  register_agent_tool.py 不存在，跳过。稍后可手动执行上述命令"
    fi
  fi
else
  info "  AI Agent ID 未配置，跳过 MCP Tool 注册步骤"
  info "  AI Agent 创建后，手动运行："
  echo "    python $SCRIPT_DIR/register_agent_tool.py \\"
  echo "        --mcp-endpoint ${MCP_FUNC_URL:-http://localhost:8000}/sse \\"
  [[ -n "$MCP_SCOPE" ]] && echo "        --mcp-scope $MCP_SCOPE \\"
  echo "        --agent-name <your-agent-name>"
fi

# ═══════════════════════════════════════════════════════════════════
# Phase 6: 生成 .env 文件
# ═══════════════════════════════════════════════════════════════════
step "Phase 6: 生成本地 .env 配置文件"

ENV_FILE="$REPO_ROOT/.env"

if ! $DRY_RUN && confirm_step "生成 ${ENV_FILE}?"; then
  # 从 .env.example 复制
  if [[ -f "$REPO_ROOT/.env.example" ]]; then
    cp "$REPO_ROOT/.env.example" "$ENV_FILE"
    info "  ✅ 已从 .env.example 复制模板"
  else
    warn "  .env.example 不存在，创建最小模板"
    cat > "$ENV_FILE" <<-EOF
# ============================================================
# micro-agent - 项目配置（由 init_new_account.sh 生成）
# ============================================================
TENANT_ID=${AZ_TENANT_ID}
CLIENT_ID=${AGENT_CLIENT_ID:-<待填写>}
CLIENT_SECRET=${AGENT_CLIENT_SECRET:-<待填写>}
MCP_SERVER_CLIENT_ID=${MCP_SERVER_CLIENT_ID:-<待填写>}
MCP_SERVER_CLIENT_SECRET=${MCP_SERVER_CLIENT_SECRET:-<待填写>}
FOUNDRY_ACCOUNT_NAME=${AI_HUB_NAME:-}
FOUNDRY_PROJECT_NAME=${AI_PROJECT_NAME:-}
FOUNDRY_RESOURCE_GROUP=${AI_RG:-}
AGENT_NAME=${AI_AGENT_ID:-}
AGENT_VERSION=latest
REDIRECT_URI=${AGENT_CLIENT_REDIRECT_URI}
SCOPE=https://ai.azure.com/.default
EOF
  fi

  # 代入实际值
  [[ -n "$AZ_TENANT_ID" ]] && local_sed "TENANT_ID" "$AZ_TENANT_ID" "$ENV_FILE"
  [[ -n "$AGENT_CLIENT_ID" && "$AGENT_CLIENT_ID" != "<"* ]] && local_sed "CLIENT_ID" "$AGENT_CLIENT_ID" "$ENV_FILE"
  [[ -n "$AGENT_CLIENT_SECRET" && "$AGENT_CLIENT_SECRET" != "<"* ]] && local_sed "CLIENT_SECRET" "$AGENT_CLIENT_SECRET" "$ENV_FILE"
  [[ -n "$MCP_SERVER_CLIENT_ID" && "$MCP_SERVER_CLIENT_ID" != "<"* ]] && local_sed "MCP_SERVER_CLIENT_ID" "$MCP_SERVER_CLIENT_ID" "$ENV_FILE"
  [[ -n "$MCP_SERVER_CLIENT_SECRET" && "$MCP_SERVER_CLIENT_SECRET" != "<"* ]] && local_sed "MCP_SERVER_CLIENT_SECRET" "$MCP_SERVER_CLIENT_SECRET" "$ENV_FILE"
  [[ -n "$AI_HUB_NAME" ]] && local_sed "FOUNDRY_ACCOUNT_NAME" "$AI_HUB_NAME" "$ENV_FILE"
  [[ -n "$AI_PROJECT_NAME" ]] && local_sed "FOUNDRY_PROJECT_NAME" "$AI_PROJECT_NAME" "$ENV_FILE"
  [[ -n "$AI_RG" ]] && local_sed "FOUNDRY_RESOURCE_GROUP" "$AI_RG" "$ENV_FILE"
  [[ -n "$AI_AGENT_ID" ]] && local_sed "AGENT_NAME" "$AI_AGENT_ID" "$ENV_FILE"
  [[ -n "$VAULT_NAME" ]] && local_sed "VAULT_NAME" "$VAULT_NAME" "$ENV_FILE"
  [[ -n "$VAULT_CONNECTION_NAME" ]] && local_sed "VAULT_CONNECTION_NAME" "$VAULT_CONNECTION_NAME" "$ENV_FILE"
  local_sed "REDIRECT_URI" "$AGENT_CLIENT_REDIRECT_URI" "$ENV_FILE"

  info "  ✅ .env 已生成 — $ENV_FILE"
  echo ""
  info "⚠️  请检查并手动填写以下敏感值（如果脚本未能自动写入）："
  info "    CLIENT_ID、CLIENT_SECRET"
  info "    MCP_SERVER_CLIENT_ID、MCP_SERVER_CLIENT_SECRET"
  echo ""
  info "  并确保 AI Foundry 相关的 Agent Identity Blueprint 已配置："
  info "    AGENT_IDENTITY_BLUEPRINT_ID"
fi

# ═══════════════════════════════════════════════════════════════════
# Phase 7: Vault & GitHub MCP Tool 配置
# ═══════════════════════════════════════════════════════════════════

VAULT_NAME=""
VAULT_CONNECTION_NAME=""
GITHUB_PAT_SECRET_NAME="github-pat"

if ! $SKIP_VAULT; then
  step "Phase 7: Vault & GitHub MCP Tool 配置"

  # ---- 7a: Azure Key Vault ----
  echo ""
  info "7a. 创建 Azure Key Vault..."
  VAULT_RG="${MCP_RG:-rg-mcp-server-${ENV_NAME}}"
  VAULT_NAME="kv-mcp-${ENV_NAME}-$(echo $RANDOM | md5sum 2>/dev/null | head -c 4 || echo $RANDOM)"
  VAULT_NAME=$(echo "$VAULT_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]//g' | cut -c1-24)

  run_az keyvault create     --name "$VAULT_NAME"     --resource-group "$VAULT_RG"     --location "$LOCATION"     --enable-rbac-authorization false

  info "  Key Vault: $VAULT_NAME (资源组: $VAULT_RG)"
  echo ""

  # ---- 7b: 存储 GitHub PAT ----
  info "7b. 存储 GitHub Personal Access Token..."
  info "  GitHub PAT 需要以下权限: Contents:Read, Issues:Read/Write, PullRequests:Read/Write, Metadata:Read"
  echo ""

  GITHUB_PAT=""
  if ! $DRY_RUN; then
    read -r -p "  输入 GitHub Personal Access Token（留空跳过，输入时不会显示）: " -s GITHUB_PAT_INPUT
    echo ""
    GITHUB_PAT="${GITHUB_PAT_INPUT:-}"
    if [[ -n "$GITHUB_PAT" ]]; then
      run_az keyvault secret set         --vault-name "$VAULT_NAME"         --name "$GITHUB_PAT_SECRET_NAME"         --value "$GITHUB_PAT"
      info "  GitHub PAT 已存储: ${VAULT_NAME}/secrets/${GITHUB_PAT_SECRET_NAME}"
    else
      warn "  跳过。稍后可手动运行: az keyvault secret set --vault-name $VAULT_NAME --name $GITHUB_PAT_SECRET_NAME --value <token>"
    fi
  fi
  echo ""

  # ---- 7c: AI Foundry Connection ----
  info "7c. 创建 AI Foundry Vault Connection..."
  echo ""
  info "  Key Vault URI: https://${VAULT_NAME}.vault.azure.net"
  echo ""

  # 尝试 REST API 自动创建
  if ! $DRY_RUN && confirm_step "尝试通过 REST API 自动创建 AI Foundry Connection?"; then
    if [[ -n "${AI_PROJECT_ENDPOINT:-}" && "${AI_PROJECT_ENDPOINT}" != "<"* ]]; then
      info "  获取 Access Token..."
      ACCESS_TOKEN=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv 2>/dev/null || echo "")
      if [[ -n "$ACCESS_TOKEN" ]]; then
        CONNECTION_NAME="kv-${VAULT_NAME}"
        CONNECTION_PAYLOAD=$(cat <<CONEOF
{
  "properties": {
    "name": "${CONNECTION_NAME}",
    "category": "AzureKeyVault",
    "target": "https://${VAULT_NAME}.vault.azure.net",
    "authType": "ManagedIdentity",
    "isShared": false,
    "metadata": {
      "resourceId": "/subscriptions/${AZ_SUBSCRIPTION_ID}/resourceGroups/${VAULT_RG}/providers/Microsoft.KeyVault/vaults/${VAULT_NAME}"
    }
  }
}
CONEOF
)
        CONNECTION_URL="${AI_PROJECT_ENDPOINT}/connections/${CONNECTION_NAME}?api-version=2025-01-01-preview"
        info "  正在创建 Connection..."
        HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X PUT           -H "Authorization: Bearer ${ACCESS_TOKEN}"           -H "Content-Type: application/json"           -d "$CONNECTION_PAYLOAD"           "$CONNECTION_URL" 2>/dev/null || echo "000")
        if [[ "$HTTP_STATUS" == "200" || "$HTTP_STATUS" == "201" ]]; then
          info "  Connection 创建成功: ${CONNECTION_NAME}"
          VAULT_CONNECTION_NAME="${CONNECTION_NAME}"
        else
          warn "  REST API 返回 ${HTTP_STATUS}。请在 Portal 中手动创建 Connection"
          info "  配置步骤:"
          info "    1. https://ai.azure.com → Project → Settings → Connections"
          info "    2. + Create → Key Vault → 选择 ${VAULT_NAME}"
        fi
      else
        warn "  无法获取 Access Token。请在 Portal 中手动创建 Connection"
      fi
    else
      warn "  AI Project Endpoint 未配置。请在 Portal 中手动创建 Connection"
    fi
  fi

  if $DRY_RUN; then
    info "[DRY-RUN] 计划创建:"
    echo "    Key Vault: ${VAULT_NAME}"
    echo "    GitHub PAT: 交互式输入并存储"
    echo "    AI Foundry Connection: REST API 或 Portal 引导"
  fi
  echo ""

  # ---- 7d: GitHub MCP Tool 配置引导 ----
  info "7d. 添加 GitHub MCP Tool 到 Agent (Portal 操作)"
  echo ""
  info "  在 https://ai.azure.com 门户中："
  echo "    1. Project → Agents → 选中你的 Agent"
  echo "    2. Tools 选项卡 → + Add → MCP tool"
  echo "    3. Name: github-mcp"
  echo "    4. Authentication: 选择 ${VAULT_CONNECTION_NAME:-kv-<vault-name>} 连接"
  echo "    5. 凭证字段: 选择 ${GITHUB_PAT_SECRET_NAME}"
  echo "    6. 保存并测试连通性"
  echo ""
  info "  常用 GitHub MCP 操作: 搜索仓库、CRUD Issue、PR 审查、读取文件"

  info "  Vault & GitHub MCP Tool 配置完成"
  info "    Key Vault:         ${VAULT_NAME}"
  echo ""

  # ---- 更新 .env 中的 Vault 配置 ----
  ENV_FILE="$REPO_ROOT/.env"
  if [[ -f "$ENV_FILE" ]]; then
    info "  更新 .env Vault 配置..."
    # 确保 vault 变量存在于 .env 中（追加如果不存在）
    for var_name in VAULT_NAME VAULT_CONNECTION_NAME GITHUB_PAT_SECRET_NAME; do
      if ! grep -q "^${var_name}=" "$ENV_FILE" 2>/dev/null; then
        echo "${var_name}=" >> "$ENV_FILE"
      fi
    done
    [[ -n "$VAULT_NAME" ]] && local_sed "VAULT_NAME" "$VAULT_NAME" "$ENV_FILE"
    [[ -n "$VAULT_CONNECTION_NAME" ]] && local_sed "VAULT_CONNECTION_NAME" "$VAULT_CONNECTION_NAME" "$ENV_FILE"
    [[ -n "$GITHUB_PAT_SECRET_NAME" ]] && local_sed "GITHUB_PAT_SECRET_NAME" "$GITHUB_PAT_SECRET_NAME" "$ENV_FILE"
    info "  .env vault vars updated"
  fi

fi

# ═══════════════════════════════════════════════════════════════════
# 最终摘要
# ═══════════════════════════════════════════════════════════════════
echo ""
banner "═══════════════════════════════════════════════════════"
banner "  初始化摘要"
banner "═══════════════════════════════════════════════════════"
echo ""
echo "  环境:     ${ENV_NAME}"
echo "  区域:     ${LOCATION}"
echo "  租户:     ${AZ_TENANT_ID}"
echo "  订阅:     ${AZ_SUBSCRIPTION_NAME} - ${AZ_SUBSCRIPTION_ID}"
echo ""

banner "  📋 Entra App Registrations"
echo "    Agent Client ID:      ${AGENT_CLIENT_ID:-未创建}"
echo "    MCP Server ID:        ${MCP_SERVER_CLIENT_ID:-未创建}"
echo ""

if [[ -n "${CONTAINER_APP_URL:-}" ]]; then
  banner "  ☁️  Container App (agent_client)"
  echo "    URL:                  ${CONTAINER_APP_URL:-}"
  echo "    名称:                 ${CONTAINER_APP_NAME:-}"
  echo "    资源组:               ${CONTAINER_RG:-}"
  echo ""
fi

if [[ -n "${MCP_FUNC_URL:-}" ]]; then
  banner "  ⚡ Function App (MCP Server)"
  echo "    名称:                 ${MCP_FUNC_NAME:-}"
  echo "    SSE Endpoint:         ${MCP_FUNC_URL:-}/sse"
  echo "    资源组:               ${MCP_RG:-}"
  echo ""
fi

if [[ -n "${AI_HUB_NAME:-}" || -n "${AI_AGENT_ID:-}" ]]; then
  banner "  🧠 AI Foundry"
  echo "    Hub:                  ${AI_HUB_NAME:-未创建}"
  echo "    Project Endpoint:     ${AI_PROJECT_ENDPOINT:-未设置}"
  echo "    Agent:                ${AI_AGENT_ID:-未创建}"
  echo "    资源组:               ${AI_RG:-}"
  echo ""
fi

banner "  🔗 本地开发启动"
echo ""
echo "  # 1. 确保 .env 已填写完整"
echo "  source ${REPO_ROOT}/.venv/bin/activate"
echo ""
echo "  # 2. 启动 MCP Server（本地开发）"
echo "  python -m mcp_server"
echo "  # → http://localhost:8000"
echo ""
echo "  # 3. 启动 Agent Client（本地开发）"
echo "  python -m agent_client.client_app"
echo "  # → http://localhost:5000"
echo ""

if [[ -n "${AI_AGENT_ID:-}" && -n "${MCP_FUNC_URL:-}" ]]; then
  banner "  🔗 MCP Tool 注册（如未在 Phase 5 完成）"
  echo ""
  echo "  python scripts/register_agent_tool.py \\"
  echo "      --mcp-endpoint ${MCP_FUNC_URL}/sse \\"
  [[ -n "$MCP_SCOPE" ]] && echo "      --mcp-scope $MCP_SCOPE \\"
  echo "      --agent-name ${AI_AGENT_ID}"
  echo ""
fi

banner "  📝 需要手动完成的步骤"
echo ""
echo "  1. AI Foundry Agent 创建（如果在 Phase 2 跳过了）："
echo "     - 打开 https://ai.azure.com"
echo "     - 进入 Project → Agents → + Create"
echo "     - 创建 Agent 并记录 Agent ID"
echo ""
echo "  2. AI Foundry Portal — Vault Connection（如果在 Phase 7 跳过了）："
echo "     - Settings → Connections → + Create → Key Vault"
echo "     - 选择 Phase 7 创建的 Key Vault: ${VAULT_NAME:-<vault-name>}"
echo ""
echo "  3. AI Foundry Portal — GitHub MCP Tool（如果在 Phase 7 跳过了）："
echo "     - Agents → 选中 Agent → Tools → + Add → MCP tool"
echo "     - 配置 GitHub 连接，使用 Vault 中的 PAT 凭证"
echo ""
echo "  4. MCP Tool 注册到 Agent（自托管 MCP Server）："
echo "     - 运行上面的 register_agent_tool.py 命令"
echo ""
echo "  5. 确保 Agent Identity Blueprint 已配置："
echo "     - AGENT_IDENTITY_BLUEPRINT_ID"
echo "     - 参考 scripts/setup_agent_identity.py"
echo ""
echo "  6. 如果 App Registration 需要 Admin Consent："
echo "     - 在 Entra ID 门户中为 API 权限授予管理员同意"
echo ""

$DRY_RUN && warn "⚠️  本次运行是 DRY-RUN 模式，未创建任何实际资源"
