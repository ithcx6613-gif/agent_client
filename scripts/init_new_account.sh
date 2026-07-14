#!/usr/bin/env bash
# ================================================================
# init_new_account.sh — One-time Azure environment bootstrap
#
# 在当前 Azure 订阅中，从零初始化本仓库所需的全部 Azure 资源。
# 适用于将项目迁移到新 Azure 账号/订阅的场景。
#
# 所需权限（需要以管理员身份运行）：
#   - Azure RBAC: Contributor 或 Owner（创建资源组、Function App 等）
#   - Microsoft Graph: Application.ReadWrite.All（创建 App Registration）
#   - AI Foundry: 需在 ai.azure.com 门户中创建 Hub/Project/Agent
#
# Usage:
#   # 1. 先查看将要创建的资源（推荐）
#   bash scripts/init_new_account.sh --dry-run
#
#   # 2. 全自动引导式创建
#   bash scripts/init_new_account.sh
#
#   # 3. 跳过某些步骤（如果已有部分资源）
#   bash scripts/init_new_account.sh --skip-foundry --skip-mcp
# ================================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# 颜色 / 日志工具
# ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
banner(){ echo -e "${CYAN}$*${NC}"; }
step()  { echo; echo -e "${CYAN}══════════════════════════════════════════${NC}"; echo -e "${CYAN}  $*${NC}"; echo -e "${CYAN}══════════════════════════════════════════${NC}"; }

# ─────────────────────────────────────────────────────────────────
# 参数解析
# ─────────────────────────────────────────────────────────────────
DRY_RUN=false
SKIP_FOUNDRY=false
SKIP_MCP=false
SKIP_CONTAINER_APP=false
ENV_NAME=""
LOCATION="eastus2"
OBO_ENABLED=false
AGENT_CLIENT_REDIRECT_URI="http://localhost:5000/callback"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)       DRY_RUN=true; shift ;;
    --skip-foundry)  SKIP_FOUNDRY=true; shift ;;
    --skip-mcp)      SKIP_MCP=true; shift ;;
    --skip-container-app) SKIP_CONTAINER_APP=true; shift ;;
    --env-name)      ENV_NAME="$2"; shift 2 ;;
    --location)      LOCATION="$2"; shift 2 ;;
    --enable-obo)    OBO_ENABLED=true; shift ;;
    --redirect-uri)  AGENT_CLIENT_REDIRECT_URI="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [options]"
      echo ""
      echo "Options:"
      echo "  --dry-run             展示将要创建的资源但不实际执行"
      echo "  --skip-foundry        跳过 AI Foundry Hub/Project 创建"
      echo "  --skip-mcp            跳过 MCP Server (Function App) 部署"
      echo "  --skip-container-app  跳过 Container App (agent_client) 部署"
      echo "  --env-name NAME       azd 环境名称（默认：自动生成）"
      echo "  --location REGION     Azure 区域（默认：eastus2）"
      echo "  --enable-obo          启用 OBO (On-Behalf-Of) 模式"
      echo "  --redirect-uri URI    Agent Client 的 OAuth 回调地址"
      exit 0
      ;;
    *) error "Unknown option: $1 (use -h for help)" ;;
  esac
done

DRY_PREFIX=""
$DRY_RUN && DRY_PREFIX="[DRY-RUN] "

# ─────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────

run_az() {
  if $DRY_RUN; then
    echo "  ${DRY_PREFIX}az $*"
    return 0
  fi
  echo "  \$ az $*"
  az "$@" || error "az command failed: $*"
}

run_azd() {
  if $DRY_RUN; then
    echo "  ${DRY_PREFIX}azd $*"
    return 0
  fi
  echo "  \$ azd $*"
  azd "$@" || error "azd command failed: $*"
}

confirm_step() {
  local prompt="$1"
  if $DRY_RUN; then
    return 0  # dry-run 模式下直接继续
  fi
  echo ""
  read -r -p "  ❓ $prompt (y/N) " response
  case "$response" in
    [yY]|[yY][eE][sS]) return 0 ;;
    *) echo "  ⏭️  跳过此步骤"; return 1 ;;
  esac
}

# ─────────────────────────────────────────────────────────────────
# Phase 0: 前置检查
# ─────────────────────────────────────────────────────────────────
step "Phase 0: 前置检查"

if $DRY_RUN; then
  info "运行在 DRY-RUN 模式 — 只展示计划，不创建任何资源"
fi

# 检查工具链
for cmd in az azd func jq python3; do
  if command -v "$cmd" &>/dev/null; then
    info "  ✅ $cmd 已安装"
  else
    error "缺少必需工具: $cmd。请先安装后再运行。"
  fi
done

# 检查 Azure 登录状态
AZ_ACCOUNT=$(az account show 2>/dev/null || true)
if [[ -z "$AZ_ACCOUNT" ]]; then
  error "未登录 Azure。请先运行: az login"
fi

AZ_TENANT_ID=$(echo "$AZ_ACCOUNT" | jq -r '.tenantId')
AZ_USER=$(echo "$AZ_ACCOUNT" | jq -r '.user.name')
AZ_SUBSCRIPTION_ID=$(echo "$AZ_ACCOUNT" | jq -r '.id')
AZ_SUBSCRIPTION_NAME=$(echo "$AZ_ACCOUNT" | jq -r '.name')

info "  租户:     $AZ_TENANT_ID"
info "  用户:     $AZ_USER"
if [[ "$AZ_SUBSCRIPTION_ID" != "null" ]]; then
  info "  订阅:     $AZ_SUBSCRIPTION_NAME ($AZ_SUBSCRIPTION_ID)"
else
  # 多订阅，让用户选择
  warn "当前有多个订阅，需选择一个"
  run_az account list --output table
  read -r -p "  输入要使用的订阅 ID: " AZ_SUBSCRIPTION_ID
  run_az account set --subscription "$AZ_SUBSCRIPTION_ID"
  info "已切换到订阅: $AZ_SUBSCRIPTION_ID"
fi

# 交互式输入环境信息
if [[ -z "$ENV_NAME" ]]; then
  read -r -p "  环境名称（如 dev, staging, prod）[dev]: " ENV_INPUT
  ENV_NAME="${ENV_INPUT:-dev}"
fi
info "  环境名称: $ENV_NAME"

read -r -p "  Azure 区域 [$LOCATION]: " LOC_INPUT
LOCATION="${LOC_INPUT:-$LOCATION}"
info "  区域:     $LOCATION"

# 资源组
MCP_RG="rg-mcp-server-${ENV_NAME}"
CONTAINER_RG="rg-${ENV_NAME}"  # azd 会自动创建 rg-{envName}

info "  MCP 资源组:      $MCP_RG"
info "  Container 资源组: $CONTAINER_RG"

echo ""
if ! confirm_step "是否继续初始化?"; then
  info "已取消"
  exit 0
fi

# ═══════════════════════════════════════════════════════════════════
# Phase 1: AI Foundry Hub + Project 创建（需要 Azure AI 权限）
# ═══════════════════════════════════════════════════════════════════
if ! $SKIP_FOUNDRY; then
  step "Phase 1: Azure AI Foundry Hub & Project"

  info "AI Foundry 创建需要以下步骤，请确保有对应权限："
  info "  - 需要 'Cognitive Services Contributor' 角色"
  info "  - 或联系订阅管理员在 https://ai.azure.com 上创建"

  AI_HUB_NAME=""
  AI_PROJECT_NAME=""
  AI_AGENT_NAME=""

  # 检查是否已有 AI Foundry 资源
  if ! $DRY_RUN; then
    EXISTING_HUBS=$(az cognitiveservices account list --query "[?kind=='AIServices'].{Name:name, RG:resourceGroup}" -o tsv 2>/dev/null || true)
    if [[ -n "$EXISTING_HUBS" ]]; then
      echo ""
      info "检测到已有的 AI Services 资源："
      echo "$EXISTING_HUBS" | while IFS=$'\t' read -r name rg; do
        echo "    - $name (资源组: $rg)"
      done
      echo ""
      if confirm_step "使用已有的 AI Services 资源?"; then
        read -r -p "  输入 AI Services 资源名称: " AI_HUB_NAME
        if [[ -z "$AI_HUB_NAME" ]]; then
          warn "未输入，将在后续步骤中手动创建"
        fi
      fi
    fi
  fi

  if [[ -z "$AI_HUB_NAME" ]]; then
    AI_HUB_NAME="hub-${ENV_NAME}"
    AI_PROJECT_NAME="proj-${ENV_NAME}"
    AI_AGENT_NAME="${ENV_NAME}-agent"

    # 确定 AI Foundry 资源组
    AI_RG="rg-ai-foundry-${ENV_NAME}"
    read -r -p "  AI Foundry 资源组 [$AI_RG]: " AI_RG_INPUT
    AI_RG="${AI_RG_INPUT:-$AI_RG}"

    # 创建 AI Services 资源
    echo ""
    info "创建 AI Services 资源（AI Foundry Hub 的基础）："
    run_az cognitiveservices account create \
      --name "$AI_HUB_NAME" \
      --resource-group "$AI_RG" \
      --location "$LOCATION" \
      --kind "AIServices" \
      --sku "S0" \
      --yes

    # 获取 endpoint
    if ! $DRY_RUN; then
      AI_HUB_ENDPOINT=$(az cognitiveservices account show \
        --name "$AI_HUB_NAME" \
        --resource-group "$AI_RG" \
        --query "properties.endpoint" -o tsv 2>/dev/null || echo "")
      info "  AI Services Endpoint: $AI_HUB_ENDPOINT"

      # 创建 AI Foundry Project（通过 REST API）
      info "创建 AI Foundry Project（需在门户中确认）..."
      info "  请前往 https://ai.azure.com 完成以下操作："
      info "    1. 进入你的 Hub → Projects → + New Project"
      info "    2. 项目名称: $AI_PROJECT_NAME"
      info "    3. Hub: 选择已创建的 $AI_HUB_NAME"
      info "    4. 完成后记录 Project Endpoint"
      echo ""
      read -r -p "  完成后输入 Project Endpoint（留空则后续手动填写）: " AI_PROJECT_ENDPOINT_INPUT
      AI_PROJECT_ENDPOINT="${AI_PROJECT_ENDPOINT_INPUT:-}"
    else
      AI_PROJECT_ENDPOINT="https://${AI_HUB_NAME}.services.ai.azure.com/api/projects/${AI_PROJECT_NAME}"
    fi

    # 创建 Agent（需在门户中完成）
    info "创建 AI Foundry Agent..."
    info "  请前往 https://ai.azure.com 在项目内创建 Agent："
    info "    1. Agents → + Create"
    info "    2. 名称: $AI_AGENT_NAME"
    info "    3. 选择一个模型（如 gpt-4o-mini）"
    info "    4. 创建后记录 Agent ID"
    echo ""
    read -r -p "  完成后输入 Agent ID（留空则后续手动填写）: " AI_AGENT_ID_INPUT
    AI_AGENT_ID="${AI_AGENT_ID_INPUT:-}"
  fi

  # 收集最终的 Foundry 配置
  if [[ -z "${AI_PROJECT_ENDPOINT:-}" ]] && ! $DRY_RUN; then
    read -r -p "  AI Project Endpoint (如 https://xxx.services.ai.azure.com/api/projects/xxx): " AI_PROJECT_ENDPOINT
  fi
  if [[ -z "${AI_AGENT_ID:-}" ]] && ! $DRY_RUN; then
    read -r -p "  AI Agent 名称: " AI_AGENT_ID
  fi
  if [[ -z "${AI_RG:-}" ]] && ! $DRY_RUN; then
    read -r -p "  AI Foundry 资源组: " AI_RG
  fi

  info "  ✅ AI Foundry 配置就绪"
  info "    Endpoint:  ${AI_PROJECT_ENDPOINT:-<待填写>}"
  info "    Agent ID:  ${AI_AGENT_ID:-<待填写>}"
  info "    资源组:    ${AI_RG:-<待填写>}"
  echo ""

  if $DRY_RUN; then
    info "[DRY-RUN] 上述步骤为交互式，dry-run 只展示不创建"
  fi
else
  # 跳过 foundry，手动收集
  AI_RG=""
  AI_PROJECT_ENDPOINT=""
  AI_AGENT_ID=""
  AI_HUB_NAME=""
fi

# ═══════════════════════════════════════════════════════════════════
# Phase 2: Entra App Registrations
# ═══════════════════════════════════════════════════════════════════
step "Phase 2: Microsoft Entra App Registrations"

# 2a: Agent Client App（SPA，用于用户 OAuth 登录）
info "2a. 创建 Agent Client SPA App Registration..."
AGENT_CLIENT_APP_NAME="micro-agent-client-${ENV_NAME}"

if ! $DRY_RUN; then
  # 检查是否已存在
  EXISTING_CLIENT_ID=$(az ad app list \
    --display-name "$AGENT_CLIENT_APP_NAME" \
    --query "[0].appId" -o tsv 2>/dev/null || true)

  if [[ -n "$EXISTING_CLIENT_ID" ]] && confirm_step "Agent Client App 已存在 (${EXISTING_CLIENT_ID})，复用?"; then
    AGENT_CLIENT_ID="$EXISTING_CLIENT_ID"
    info "  复用了已有 App: $AGENT_CLIENT_ID"
  else
    AGENT_CLIENT_ID=$(run_az ad app create \
      --display-name "$AGENT_CLIENT_APP_NAME" \
      --sign-in-audience "AzureADMyOrg" \
      --web-redirect-uris "$AGENT_CLIENT_REDIRECT_URI" \
      --query "appId" -o tsv)

    info "  Agent Client App ID: $AGENT_CLIENT_ID"
  fi
else
  AGENT_CLIENT_ID="<待创建: $AGENT_CLIENT_APP_NAME>"
fi

# 添加 API 权限
if [[ -n "${AGENT_CLIENT_ID:-}" && "$AGENT_CLIENT_ID" != "<"* ]]; then
  info "  添加 API 权限（Azure AI Foundry + Microsoft Graph）..."
  # Azure AI Foundry（https://ai.azure.com）
  run_az ad app permission add \
    --id "$AGENT_CLIENT_ID" \
    --api "https://ai.azure.com" \
    --api-permissions "1a7925b5-f871-417a-9b8b-303f9f29fa10=Scope" 2>/dev/null || \
    warn "  Azure AI Foundry 权限添加可能失败（需要 admin 同意），可在门户手动添加"

  # Microsoft Graph User.Read
  run_az ad app permission add \
    --id "$AGENT_CLIENT_ID" \
    --api "00000003-0000-0000-c000-000000000000" \
    --api-permissions "e1fe6dd8-ba31-4d61-89e7-88639da4683d=Scope" 2>/dev/null || \
    warn "  Microsoft Graph 权限添加失败，可在门户手动添加"

  # 生成 Client Secret
  info "  生成 Client Secret..."
  AGENT_CLIENT_SECRET=$(run_az ad app credential reset \
    --id "$AGENT_CLIENT_ID" \
    --display-name "client-secret-${ENV_NAME}" \
    --years "1" \
    --query "password" -o tsv 2>/dev/null || echo "")
  if [[ -z "$AGENT_CLIENT_SECRET" ]]; then
    warn "  Client Secret 生成失败（只读账号），请在 Azure 门户手动添加"
    AGENT_CLIENT_SECRET="<请在 Azure 门户创建>"
  fi
fi

# 2b: MCP Server App（用于 OBO Token 交换）
echo ""
info "2b. 创建 MCP Server App Registration..."
MCP_SERVER_APP_NAME="micro-agent-mcp-server-${ENV_NAME}"

if ! $DRY_RUN; then
  EXISTING_MCP_ID=$(az ad app list \
    --display-name "$MCP_SERVER_APP_NAME" \
    --query "[0].appId" -o tsv 2>/dev/null || true)

  if [[ -n "$EXISTING_MCP_ID" ]] && confirm_step "MCP Server App 已存在 (${EXISTING_MCP_ID})，复用?"; then
    MCP_SERVER_CLIENT_ID="$EXISTING_MCP_ID"
    info "  复用了已有 App: $MCP_SERVER_CLIENT_ID"
  else
    MCP_SERVER_CLIENT_ID=$(run_az ad app create \
      --display-name "$MCP_SERVER_APP_NAME" \
      --sign-in-audience "AzureADMyOrg" \
      --query "appId" -o tsv)

    info "  MCP Server App ID: $MCP_SERVER_CLIENT_ID"

    # 设置 identifier URI
    MCP_APP_URI="api://${MCP_SERVER_CLIENT_ID}"
    run_az ad app update \
      --id "$MCP_SERVER_CLIENT_ID" \
      --identifier-uris "$MCP_APP_URI"

    # 暴露 access_as_user scope
    SCOPE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
    MCP_SCOPE_NAME="access_as_user"
    run_az ad app update \
      --id "$MCP_SERVER_CLIENT_ID" \
      --set "api.oauth2PermissionScopes=[{\"id\":\"${SCOPE_ID}\",\"value\":\"${MCP_SCOPE_NAME}\",\"type\":\"User\",\"isEnabled\":true,\"userConsentDisplayName\":\"Access MCP Server as user\",\"userConsentDescription\":\"Allows the app to access MCP Server on your behalf\"}]"

    # 生成 Client Secret
    info "  生成 MCP Server Client Secret..."
    MCP_SERVER_CLIENT_SECRET=$(run_az ad app credential reset \
      --id "$MCP_SERVER_CLIENT_ID" \
      --display-name "mcp-server-secret-${ENV_NAME}" \
      --years "1" \
      --query "password" -o tsv 2>/dev/null || echo "")
    if [[ -z "$MCP_SERVER_CLIENT_SECRET" ]]; then
      warn "  Client Secret 生成失败，请在 Azure 门户手动添加"
      MCP_SERVER_CLIENT_SECRET="<请在 Azure 门户创建>"
    fi
  fi
else
  MCP_SERVER_CLIENT_ID="<待创建: $MCP_SERVER_APP_NAME>"
  MCP_APP_URI="api://<mcp-server-app-id>"
fi

echo ""
info "  ✅ App Registrations 就绪"
info "    Agent Client ID:     ${AGENT_CLIENT_ID:-<待填写>}"
info "    MCP Server ID:       ${MCP_SERVER_CLIENT_ID:-<待填写>}"

# ═══════════════════════════════════════════════════════════════════
# Phase 3: MCP Server — Azure Functions 部署
# ═══════════════════════════════════════════════════════════════════
if ! $SKIP_MCP; then
  step "Phase 3: MCP Server — Azure Functions 部署"

  # 创建资源组
  info "创建资源组 ${MCP_RG}..."
  run_az group create --name "$MCP_RG" --location "$LOCATION"

  # 存储账号
  MCP_STORAGE="mcpstore${ENV_NAME}$(echo $RANDOM | md5sum 2>/dev/null | head -c 6 || echo $RANDOM)"
  info "创建 Storage Account ${MCP_STORAGE}..."
  run_az storage account create \
    --name "$MCP_STORAGE" \
    --resource-group "$MCP_RG" \
    --location "$LOCATION" \
    --sku "Standard_LRS" \
    --allow-blob-public-access false

  # Function App
  MCP_FUNC_NAME="func-mcp-${ENV_NAME}"
  info "创建 Function App ${MCP_FUNC_NAME}..."
  run_az functionapp create \
    --name "$MCP_FUNC_NAME" \
    --resource-group "$MCP_RG" \
    --storage-account "$MCP_STORAGE" \
    --consumption-plan-location "$LOCATION" \
    --runtime "python" \
    --runtime-version "3.12" \
    --functions-version "4" \
    --os-type "Linux"

  # 启用系统托管标识
  info "启用系统托管标识..."
  MCP_MI_PRINCIPAL_ID=$(run_az functionapp identity assign \
    --name "$MCP_FUNC_NAME" \
    --resource-group "$MCP_RG" \
    --query "principalId" -o tsv)

  # 设置环境变量
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

  # 创建 User-Assigned Managed Identity（用于 OBO）
  if $OBO_ENABLED; then
    echo ""
    info "创建 User-Assigned Managed Identity..."
    MCP_UMI_NAME="umi-mcp-${ENV_NAME}"
    MCP_UMI_JSON=$(run_az identity create \
      --name "$MCP_UMI_NAME" \
      --resource-group "$MCP_RG" \
      -o json || warn "UMI 可能已存在")
    MCP_UMI_CLIENT_ID=$(echo "$MCP_UMI_JSON" | jq -r '.clientId // empty')
    MCP_UMI_PRINCIPAL_ID=$(echo "$MCP_UMI_JSON" | jq -r '.principalId // empty')

    # 创建 Federated Identity Credential
    if [[ -n "${MCP_UMI_CLIENT_ID:-}" && "${MCP_SERVER_CLIENT_ID:-}" != "<"* ]]; then
      info "创建 Federated Identity Credential..."
      FIC_NAME="umi-mcp-fic-${ENV_NAME}"
      if ! $DRY_RUN; then
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
        run_az rest --method POST \
          --uri "https://graph.microsoft.com/v1.0/applications(appId='${MCP_SERVER_CLIENT_ID}')/federatedIdentityCredentials" \
          --headers "Content-Type=application/json" \
          --body "$FIC_BODY" 2>/dev/null || \
          warn "FIC 创建可能已存在（忽略）"
      fi
    fi
  fi

  # 部署 Function App 代码
  echo ""
  info "部署 MCP Server 代码到 Function App..."
  if ! $DRY_RUN; then
    if confirm_step "立即部署 MCP Server 代码?"; then
      SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
      REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
      cd "$REPO_ROOT"

      # 安装 Python 依赖到 Function App
      info "安装 Python 依赖..."
      run_az functionapp config set \
        --name "$MCP_FUNC_NAME" \
        --resource-group "$MCP_RG" \
        --linux-fx-version "PYTHON|3.12"

      # 发布
      run_az functionapp deployment source config-zip \
        --name "$MCP_FUNC_NAME" \
        --resource-group "$MCP_RG" \
        --src <(cd "$REPO_ROOT" && zip -r -x '*.git*' -x '*.venv*' -x '__pycache__*' -x 'agent_client/*' -x 'node_modules/*' -r /dev/stdin .) \
        2>/dev/null || {
          warn "zip 部署方式不可用，请手动运行:"
          echo "    cd $REPO_ROOT"
          echo "    func azure functionapp publish $MCP_FUNC_NAME --python"
        }
      cd - >/dev/null
    else
      info "跳过部署。稍后可以运行："
      info "    cd $(cd "$(dirname "$0")/.." && pwd)"
      info "    func azure functionapp publish $MCP_FUNC_NAME --python"
    fi
  fi

  MCP_FUNC_URL="https://${MCP_FUNC_NAME}.azurewebsites.net"
  info "  ✅ MCP Server 部署就绪"
  info "     Function App: ${MCP_FUNC_NAME}"
  info "     SSE Endpoint: ${MCP_FUNC_URL}/sse"

else
  MCP_FUNC_NAME=""
  MCP_FUNC_URL=""
fi

# ═══════════════════════════════════════════════════════════════════
# Phase 4: Container App (agent_client) 部署
# ═══════════════════════════════════════════════════════════════════
if ! $SKIP_CONTAINER_APP; then
  step "Phase 4: Container App (agent_client) 部署"

  # 确保 azd 已登录
  info "检查 azd 登录状态..."
  if ! $DRY_RUN; then
    AZD_AUTH_STATUS=$(azd auth show 2>/dev/null || echo "not logged in")
    if [[ "$AZD_AUTH_STATUS" == "not logged in" ]]; then
      info "登录 azd (使用与 az 相同的身份)..."
      run_azd auth login
    else
      info "azd 已登录"
    fi
  fi

  # 进入 agent_client 目录
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  AGENT_CLIENT_DIR="$(cd "$SCRIPT_DIR/../agent_client" && pwd 2>/dev/null)" || {
    warn "agent_client 目录不存在，跳过 Container App 部署"
    SKIP_CONTAINER_APP=true
  }

  if [[ -d "$AGENT_CLIENT_DIR" ]]; then
    # 初始化 azd 环境
    info "初始化 azd 环境..."
    if ! $DRY_RUN; then
      cd "$AGENT_CLIENT_DIR"

      # 检查是否已有 azd 环境
      EXISTING_ENV=$(azd env list 2>/dev/null | grep "$ENV_NAME" || true)
      if [[ -z "$EXISTING_ENV" ]]; then
        run_azd env new "$ENV_NAME" --location "$LOCATION"
        info "  已创建新 azd 环境: $ENV_NAME"
      else
        run_azd env select "$ENV_NAME"
        info "  已选择已有环境: $ENV_NAME"
      fi

      # 设置必要的环境变量
      info "设置 azd 环境变量..."

      # AI Foundry
      if [[ -n "${AI_PROJECT_ENDPOINT:-}" ]]; then
        run_azd env set AI_AGENT_ENDPOINT "$AI_PROJECT_ENDPOINT"
      fi
      if [[ -n "${AI_AGENT_ID:-}" ]]; then
        run_azd env set AI_AGENT_ID "$AI_AGENT_ID"
      fi
      if [[ -n "${AI_RG:-}" ]]; then
        run_azd env set AI_FOUNDRY_RESOURCE_GROUP "$AI_RG"
      fi
      if [[ -n "${AI_HUB_NAME:-}" ]]; then
        run_azd env set AI_FOUNDRY_RESOURCE_NAME "$AI_HUB_NAME"
      fi

      # Entra
      run_azd env set ENTRA_TENANT_ID "$AZ_TENANT_ID"
      run_azd env set ENABLE_OBO "$( [[ "$OBO_ENABLED" == "true" ]] && echo "true" || echo "false" )"

      # 验证配置
      echo ""
      info "azd 环境变量设置完成："
      run_azd env get-values | grep -E '(AI_|ENTRA|ENABLE)' || echo "  (环境变量列表待验证)"

      # 运行 azd up
      echo ""
      if confirm_step "运行 azd up 进行完整部署（约 10-15 分钟）?"; then
        info "开始部署 Container App..."
        run_azd up
        info "  ✅ Container App 部署完成"

        # 获取部署后的 URL
        CONTAINER_APP_URL=$(azd env get-value WEB_ENDPOINT 2>/dev/null || echo "")
        info "  Container App URL: ${CONTAINER_APP_URL:-<获取失败>}"
      else
        info "跳过部署。稍后可以运行："
        info "    cd $AGENT_CLIENT_DIR"
        info "    azd up"
      fi

      cd - >/dev/null
    else
      info "[DRY-RUN] 会执行以下操作："
      echo "    cd $AGENT_CLIENT_DIR"
      echo "    azd env new $ENV_NAME --location $LOCATION"
      echo "    azd env set AI_AGENT_ENDPOINT ..."
      echo "    azd env set AI_AGENT_ID ..."
      echo "    azd env set AI_FOUNDRY_RESOURCE_GROUP ..."
      echo "    azd env set AI_FOUNDRY_RESOURCE_NAME ..."
      echo "    azd env set ENTRA_TENANT_ID $AZ_TENANT_ID"
      echo "    azd env set ENABLE_OBO $([[ "$OBO_ENABLED" == "true" ]] && echo "true" || echo "false")"
      echo "    azd up"
    fi
  fi
fi

# ═══════════════════════════════════════════════════════════════════
# Phase 5: 集成配置
# ═══════════════════════════════════════════════════════════════════
step "Phase 5: 集成配置"

# 5a: 注册 MCP Tool 到 AI Foundry Agent
if [[ -n "${AI_PROJECT_ENDPOINT:-}" && -n "${AI_AGENT_ID:-}" ]]; then
  echo ""
  info "5a. 注册 MCP Tool 到 AI Agent..."
  echo ""
  info "需要运行以下命令："
  if [[ -n "${MCP_SERVER_CLIENT_ID:-}" && "$MCP_SERVER_CLIENT_ID" != "<"* ]]; then
    MCP_SCOPE="api://${MCP_SERVER_CLIENT_ID}/access_as_user"
  else
    MCP_SCOPE=""
  fi
  echo ""
  echo "    python scripts/register_agent_tool.py \\"
  echo "        --mcp-endpoint ${MCP_FUNC_URL:-<mcp-server-url>}/sse \\"
  if [[ -n "$MCP_SCOPE" ]]; then
    echo "        --mcp-scope $MCP_SCOPE \\"
  fi
  echo "        --agent-name ${AI_AGENT_ID}"
  echo ""
  if ! $DRY_RUN; then
    if confirm_step "立即注册 MCP Tool?"; then
      SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
      MCP_ENDPOINT="${MCP_FUNC_URL:-http://localhost:8000}/sse"
      cd "$(cd "$SCRIPT_DIR/.." && pwd)"

      if [[ -n "$MCP_SCOPE" ]]; then
        run_az python3 scripts/register_agent_tool.py \
          --mcp-endpoint "$MCP_ENDPOINT" \
          --mcp-scope "$MCP_SCOPE" \
          --agent-name "$AI_AGENT_ID" || warn "MCP 工具注册失败（可能 agent 尚未就绪）"
      else
        run_az python3 scripts/register_agent_tool.py \
          --mcp-endpoint "$MCP_ENDPOINT" \
          --agent-name "$AI_AGENT_ID" || warn "MCP 工具注册失败"
      fi
    fi
  fi
fi

# ═══════════════════════════════════════════════════════════════════
# Phase 6: 生成 .env 文件
# ═══════════════════════════════════════════════════════════════════
step "Phase 6: 生成本地 .env 配置文件"

ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"

if ! $DRY_RUN && confirm_step "生成 ${ENV_FILE}?"; then

  # 从 .env.example 复制
  if [[ -f "${ENV_FILE}.example" ]]; then
    cp "${ENV_FILE}.example" "${ENV_FILE}"
  fi

  # 代入真实值（只替换已知变量，保留注释和未知变量）
  local_sed_inplace() {
    local pattern="$1"
    local file="$2"
    if [[ "$(uname)" == "Darwin" ]]; then
      sed -i '' "$pattern" "$file"
    else
      sed -i "$pattern" "$file"
    fi
  }

  for kv in \
    "TENANT_ID=$AZ_TENANT_ID" \
    "FOUNDRY_ACCOUNT_NAME=${AI_HUB_NAME:-}" \
    "FOUNDRY_PROJECT_NAME=${AI_PROJECT_NAME:-}" \
    "FOUNDRY_RESOURCE_GROUP=${AI_RG:-}" \
    "AI_PROJECT_ENDPOINT=${AI_PROJECT_ENDPOINT:-}" \
    "AGENT_NAME=${AI_AGENT_ID:-}" \
    "REDIRECT_URI=$AGENT_CLIENT_REDIRECT_URI"; do

    KEY="${kv%%=*}"
    VALUE="${kv#*=}"
    if [[ -n "$VALUE" ]]; then
      local_sed_inplace "s|^${KEY}=.*|${KEY}=${VALUE}|" "$ENV_FILE" 2>/dev/null || true
    fi
  done

  info "  ✅ .env 已生成 — $ENV_FILE"
  info "  请手动填写以下敏感值："
  info "    CLIENT_ID                   = ${AGENT_CLIENT_ID:-<待填写>}"
  info "    CLIENT_SECRET               = ${AGENT_CLIENT_SECRET:-<待填写>}"
  info "    MCP_SERVER_CLIENT_ID        = ${MCP_SERVER_CLIENT_ID:-<待填写>}"
  info "    MCP_SERVER_CLIENT_SECRET    = ${MCP_SERVER_CLIENT_SECRET:-<待填写>}"
fi

# ═══════════════════════════════════════════════════════════════════
# 最终摘要
# ═══════════════════════════════════════════════════════════════════
echo ""
banner "═══════════════════════════════════════════════════════"
banner "  初始化摘要"
banner "═══════════════════════════════════════════════════════"
echo ""
echo "  租户:           $AZ_TENANT_ID"
echo "  订阅:           $AZ_SUBSCRIPTION_NAME ($AZ_SUBSCRIPTION_ID)"
echo "  环境:           $ENV_NAME"
echo "  区域:           $LOCATION"
echo ""

banner "  📋 Entra App Registrations"
echo "    Agent Client ID:    ${AGENT_CLIENT_ID:-<未创建>}"
echo "    Agent Client Secret: ${AGENT_CLIENT_SECRET:+已设置}"
echo "    MCP Server ID:      ${MCP_SERVER_CLIENT_ID:-<未创建>}"
echo "    MCP Server Secret:  ${MCP_SERVER_CLIENT_SECRET:+已设置}"
echo ""

if [[ -n "${MCP_FUNC_NAME:-}" ]]; then
  banner "  ☁️  MCP Server (Azure Functions)"
  echo "    Function App:       $MCP_FUNC_NAME"
  echo "    资源组:             ${MCP_RG:-}"
  echo "    SSE Endpoint:       ${MCP_FUNC_URL:-}/sse"
  echo ""
fi

if [[ -n "${AI_PROJECT_ENDPOINT:-}" || -n "${AI_AGENT_ID:-}" ]]; then
  banner "  🧠 AI Foundry"
  echo "    Project Endpoint:   ${AI_PROJECT_ENDPOINT:-<未设置>}"
  echo "    Agent ID:           ${AI_AGENT_ID:-<未设置>}"
  echo "    AI 资源组:          ${AI_RG:-<未设置>}"
  echo ""
fi

banner "  🔗 下一步操作"
echo ""
echo "  1. 本地开发环境：将上面记录的 ID/Secret 填入 .env 文件"
echo ""
echo "  2. 启动本地 MCP Server："
echo "       source .venv/bin/activate"
echo "       python -m mcp_server"
echo "     → http://localhost:8000"
echo ""
echo "  3. 启动本地 Agent Client："
echo "       python -m agent_client.client_app"
echo "     → http://localhost:5000"
echo ""
echo "  4. AI Foundry Agent Tool 注册："
echo "       python scripts/register_agent_tool.py \\"
echo "           --mcp-endpoint ${MCP_FUNC_URL:-http://localhost:8000}/sse"
echo ""
echo "  5. 访问 AI Foundry 验证 Agent 功能："
echo "       https://ai.azure.com"
echo ""

if $DRY_RUN; then
  echo ""
  warn "⚠️  本次运行是 DRY-RUN 模式（未创建任何资源）"
  warn "   移除 --dry-run 参数以实际创建"
  echo ""
fi
