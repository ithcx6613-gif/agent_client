# micro-agent

**Azure AI Foundry Agent Demo** — 一个基于 Azure AI Foundry 的 Agent 框架演示项目。

![Python](https://img.shields.io/badge/Python-3.13+-blue)
![Azure AI Foundry](https://img.shields.io/badge/Azure%20AI%20Foundry-Agent-blue)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 项目概述

本项目演示了如何在 Azure AI Foundry 上构建一个完整的 Agent 应用，包含三个核心组件：

| 组件 | 目录 | 说明 |
|------|------|------|
| **Agent Client** | `agent_client/` | Flask Web 应用 — 用户通过 OAuth 登录后，与 AI Foundry Agent 对话 |
| **MCP Server** | `mcp_server/` | FastMCP 服务 — 为 Agent 提供可调用的 Tool (snippet管理、用户信息等) |
| **Setup Scripts** | `scripts/` | Azure AD 配置脚本 — 自动创建 App Registration、Identity 等基础设施 |

### 架构

```
用户浏览器
   │
   ├─ OAuth 登录 ──→ Microsoft Entra ID
   │
   ▼
Agent Client (Flask, :5000)
   │
   ├─ 用户 OAuth Token → Azure AI Foundry Agent API
   │
   ▼
AI Foundry Agent
   │
   ├─ MCP Tool 调用 ──→ MCP Server (FastMCP, :8000)
   │                       │
   │                       ├─ hello_mcp / server_info
   │                       ├─ save_snippet / get_snippet / list_snippets
   │                       ├─ whoami / get_current_user (OBO → Graph)
   │                       └─ batch_save_snippets
   │
   └─ (OBO Token Exchange) → Microsoft Graph API
```

---

## 快速开始

### 前置条件

- Python 3.13+
- Azure 订阅 + [Azure AI Foundry hub/project](https://ai.azure.com)
- 已注册的 [Agent Identity Blueprint](https://learn.microsoft.com/entra/identity/agents/agent-identity)
- Azure CLI (`az`) 已登录

### 环境准备

```bash
# 1. 克隆项目
git clone <repo-url> && cd micro-agent

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 填入实际值（或运行 setup 脚本自动生成）
```

### 配置 Azure AD App Registration

方式一：自动创建（推荐）

```bash
# 查看将要创建的资源
python scripts/setup_app_registration.py --dry-run

# 自动创建两个 App Registration + 客户端密钥
python scripts/setup_app_registration.py \
    --tenant-id $(az account show --query tenantId -o tsv)
```

方式二：手动配置

1. 在 [Azure Portal > App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps) 中：
   - 创建 **Agent Client App** (micro-agent-client)
     - Platform: Web, Redirect URI: `http://localhost:5000/callback`
     - API Permissions: `https://ai.azure.com/.default` + `Microsoft Graph User.Read`
     - 生成 client secret
   - 创建 **MCP Server App** (micro-agent-mcp-server)
     - Expose an API → Add scope `access_as_user`
     - Pre-authorize 上面的 client app
2. 将值填入 `.env`

### 设置 Agent Identity

```bash
# 为 MCP Server 创建 User-Assigned Managed Identity + Federated Credential
chmod +x scripts/setup_agent_identity.sh
./scripts/setup_agent_identity.sh \
    -t $(az account show --query tenantId -o tsv) \
    -s $(az account show --query id -o tsv) \
    -g <your-resource-group> \
    -a <mcp-server-app-client-id>

# 创建 Agent Identity Blueprint
python scripts/setup_agent_identity.py \
    --mi-client-id <managed-identity-client-id>
```

### 启动服务

#### 启动 Agent Client

```bash
# 启动 Flask Web 应用 (OAuth 登录 + Agent 对话)
python -m agent_client.client_app
# → http://localhost:5000
```

#### 启动 MCP Server

```bash
# 启动 MCP Tool Server (SSE 传输)
python -m mcp_server
# → http://localhost:8000 (SSE endpoint)
```

也可以使用 stdio 传输模式:

```bash
MCP_TRANSPORT=stdio python -m mcp_server
```

### 将 MCP Server 注册为 Agent Tool

```bash
# 注册 MCP Server 到 AI Foundry Agent
python scripts/register_agent_tool.py \
    --mcp-endpoint http://localhost:8000

# 如需指定 OAuth scope:
python scripts/register_agent_tool.py \
    --mcp-endpoint https://your-mcp-server.azurewebsites.net \
    --mcp-scope api://<mcp-server-app-id>/access_as_user
```

---

## 项目结构

```
micro-agent/
├── agent_client/                    # Agent 客户端 (Flask)
│   ├── __init__.py                  # 包入口
│   ├── agent_client.py              # AIProjectClient + OpenAI 客户端封装
│   ├── auth_helper.py               # MSAL OAuth 授权辅助
│   ├── client_app.py                # Flask 应用 + 路由
│   └── templates/
│       └── index.html               # OAuth 登录 + 对话界面 UI
│
├── mcp_server/                      # MCP Server (FastMCP)
│   ├── __init__.py
│   ├── __main__.py                  # python -m mcp_server 入口
│   ├── server.py                    # FastMCP 服务创建 + 工具注册
│   ├── config.py                    # 环境配置加载
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── hello_tool.py           # hello_mcp / server_info 工具
│   │   ├── snippet_tools.py        # 代码片段 CRUD 工具
│   │   ├── user_info_tool.py       # 用户信息 / OBO Graph API 工具
│   │   └── batch_tools.py          # 批量操作工具
│   └── auth/
│       ├── __init__.py
│       └── obo_auth.py             # OBO Token 交换 + JWT 解码工具
│
├── scripts/                         # 基础设施脚本
│   ├── setup_app_registration.py    # 自动创建 Azure AD App Registration
│   ├── setup_agent_identity.sh      # 创建 Managed Identity + Federated Credential
│   ├── setup_agent_identity.py      # 创建 Agent Identity Blueprint
│   └── register_agent_tool.py       # 注册 MCP Server 到 AI Foundry Agent
│
├── requirements.txt                 # Python 依赖
├── .env.example                     # 环境变量模板
└── README.md                        # 本文档
```

---

## 详解各组件

### 1. Agent Client (`agent_client/`)

用户通过浏览器访问 Flask Web 应用，使用 Microsoft 账号 OAuth 登录后，即可与 Azure AI Foundry Agent 对话。

**核心流程：**

1. **AuthHelper** 使用 MSAL ConfidentialClientApplication 发起 OAuth 授权码流程
2. 用户登录 Microsoft Entra ID 并同意授权
3. 回调接收授权码，兑换为 Access Token（scope: `https://ai.azure.com/.default`）
4. **AgentClient** 持有该 Token，调用 AI Foundry Agent 的 Responses API
5. 前端实时显示对话结果

**端点：**

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 登录 / 对话页面 |
| `/auth/login` | GET | 跳转到 Microsoft OAuth 授权页 |
| `/auth/status` | GET | 检查当前登录状态 |
| `/auth/logout` | POST | 退出登录 |
| `/callback` | GET | OAuth 回调接收端 |
| `/api/agent/check` | GET | 检查 Agent 连通性 |
| `/api/ai/chat` | POST | 发送消息给 Agent |

### 2. MCP Server (`mcp_server/`)

基于 FastMCP 框架构建的 Tool Server，Agent 在对话中可以根据需要自动调用这些工具。

**工具清单：**

| 工具 | 说明 | 来源参考 |
|------|------|----------|
| `hello_mcp` | 基础问候，确认服务运行 | FunctionsMcpTool |
| `server_info` | 返回服务器版本和可用工具列表 | FunctionsMcpTool |
| `save_snippet` | 保存代码片段 | FunctionsMcpTool - save_snippet |
| `get_snippet` | 按名称获取片段 | FunctionsMcpTool - get_snippet |
| `list_snippets` | 列出所有片段 | FunctionsMcpTool |
| `delete_snippet` | 删除片段 | FunctionsMcpTool |
| `get_snippet_with_metadata` | 获取片段 + 元数据 (JSON) | FunctionsMcpTool - get_snippet_with_metadata |
| `whoami` | 从 Token 中提取用户身份 | FunctionsMcpTool - hello_tool_with_auth |
| `get_current_user` | 通过 OBO 调用 Graph API 获取用户信息 | FunctionsMcpTool - hello_tool_with_auth |
| `batch_save_snippets` | 批量保存 (JSON 数组输入) | FunctionsMcpTool - batch_save_snippets |
| `batch_get_snippets` | 批量获取 (返回 JSON) | FunctionsMcpTool |

**OBO (On-Behalf-Of) 认证流程：**

当 Agent 调用 `get_current_user` 工具时：

1. Agent 将用户的 OAuth Token 传递给 MCP Server
2. MCP Server 使用该 Token 向 Microsoft Graph 发起请求
3. Graph 验证 Token 并返回用户信息（displayName, email 等）
4. 工具结果返回给 Agent，Agent 继续与用户对话

在生产部署中，配合 Azure Easy Auth + Managed Identity Federated Credential，可以使用真正的 OBO 流程：
- Managed Identity 生成 client assertion
- OnBehalfOfCredential 用用户 Token + assertion 交换 Graph Token
- 详见 `mcp_server/auth/obo_auth.py`

### 3. Setup Scripts (`scripts/`)

| 脚本 | 作用 |
|------|------|
| `setup_app_registration.py` | 自动创建 Agent Client 和 MCP Server 两个 App Registration |
| `setup_agent_identity.sh` | 创建 User-Assigned Managed Identity + Federated Identity Credential |
| `setup_agent_identity.py` | 创建 Agent Identity Blueprint (Entra ID) |
| `register_agent_tool.py` | 将 MCP Server 注册到 AI Foundry Agent 的 Tool Definition |

---

## 部署到 Azure

### MCP Server 部署

MCP Server 可以部署到 Azure Container Apps、Azure App Service 或 Azure Functions：

```bash
# 使用 Azure Developer CLI 部署
azd up
```

部署后更新 MCP Server 配置：
```bash
python scripts/register_agent_tool.py \
    --mcp-endpoint https://your-mcp-server.azurewebsites.net \
    --mcp-scope api://<mcp-server-app-id>/access_as_user
```

### Agent Client 部署

```bash
# 更新 REDIRECT_URI 为生产地址
# 部署到 Azure App Service / Container Apps
# 设置 GitHub Actions 或 Azure DevOps CI/CD
```

---

## 参考项目

本项目参考了 [remote-mcp-functions-python](https://github.com/Azure-Samples/remote-mcp-functions-python) 的架构和工具设计思路，将其 MCP Tool Server 的模式移植到 FastMCP 框架中，并与 Azure AI Foundry Agent 集成。

关键参考：
- **FunctionsMcpTool** → `mcp_server/tools/*.py` — MCP Tool 定义模式
- **FunctionsMcpTool/hello_tool_with_auth** → OBO 认证流程
- **infra/app/entra.bicep** → App Registration 配置 (Bicep → Python 脚本)
- Architecture: Remote MCP with built-in authentication via Microsoft Entra ID

---

## 开发

### 验证 Import

```bash
source .venv/bin/activate
python -c "from mcp_server.server import create_app; app = create_app(); print('OK:', type(app).__name__)"
python -c "from agent_client import AgentClient, AuthHelper; print('OK:', AgentClient.__name__, AuthHelper.__name__)"
```

### 测试 MCP Server

```bash
# 启动服务器
python -m mcp_server &
sleep 2

# 用 curl 测试 SSE 连接
curl -N http://localhost:8000/sse

# 或用 MCP Inspector 测试
npx @modelcontextprotocol/inspector http://localhost:8000
```

### 代码风格

```bash
pip install ruff
ruff check mcp_server/ agent_client/ scripts/
```

---

## 许可证

MIT
