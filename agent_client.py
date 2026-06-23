from azure.identity import OnBehalfOfCredential, ClientSecretCredential
from azure.core.credentials import AccessToken
from azure.ai.projects import AIProjectClient
from typing import Optional
import os
from dotenv import load_dotenv
import base64
import json, time
from datetime import datetime

load_dotenv()

class StaticTokenCredential:
    def __init__(self, user_token:str):
        self.user_token = user_token

    def decode_jwt(self):
        try:
            # 拆分Token为三部分
            header_b64, payload_b64, signature = self.user_token.split('.')

            # Base64URL解码（替换字符 + 补全填充符）
            def b64url_decode(s):
                # Base64URL vs 标准Base64：-→+，_→/，补全=
                s += '=' * (4 - len(s) % 4)
                return base64.urlsafe_b64decode(s)
            
            # 解析Header
            header = json.loads(b64url_decode(header_b64))
            # 解析Payload
            payload = json.loads(b64url_decode(payload_b64))
            
            # 转换时间戳为可读格式（北京时间，+8小时）
            for key in ['iat', 'nbf', 'exp']:
                if key in payload:
                    payload[f'{key}_cn'] = datetime.fromtimestamp(payload[key] + 8*3600).strftime('%Y-%m-%d %H:%M:%S')
            
            return {
                'header': header,
                'payload': payload,
                'signature': signature  # 签名不解析，仅展示
            }
        except Exception as e:
            return f"解析失败：{str(e)}"

    def get_token(self, *scopes, **kwargs):
        print(f"request args ={scopes}, kwargs={kwargs}")
        token_payload = self.decode_jwt().get("payload")
        aud = token_payload.get('aud', '')
        
        # 尝试匹配：token的aud可能是一个资源URL（如 https://ai.azure.com），
        # 而SDK请求的scope可能是 https://cognitiveservices.azure.com/.default
        # 需要检查aud是否是请求scope的基础资源
        for scope in scopes:
            # 直接匹配
            if aud == scope or aud in scope:
                print(f"decode token payload, user = {token_payload.get('name')}, aud={aud}, expired at {token_payload.get('exp')}")
                return AccessToken(self.user_token, token_payload['exp'])
            # 去掉 /.default 后缀后匹配
            scope_base = scope.rstrip('/.default')
            if aud == scope_base or aud in scope_base:
                print(f"decode token payload, user = {token_payload.get('name')}, aud={aud}, expired at {token_payload.get('exp')}")
                return AccessToken(self.user_token, token_payload['exp'])
            # 检查aud是否是scope的父资源（Azure AI资源通用scope）
            if 'azure.com' in aud and 'azure.com' in scope:
                # 如果aud是Azure资源URL，scope也是Azure资源URL，且aud是scope的前缀或相同域
                aud_domain = aud.rstrip('/').split('/')[2] if '/' in aud else aud
                scope_domain = scope_base.rstrip('/').split('/')[2] if '/' in scope_base else scope_base
                if aud_domain == scope_domain:
                    print(f"decode token payload, user = {token_payload.get('name')}, aud={aud}, expired at {token_payload.get('exp')}")
                    return AccessToken(self.user_token, token_payload['exp'])
        
        # 兜底：如果所有scope都不匹配，返回token（Azure AI SDK可能对scope检查不严格）
        print(f"decode token payload, user = {token_payload.get('name')}, aud={aud}, expired at {token_payload.get('exp')}, scopes_requested={scopes}")
        return AccessToken(self.user_token, token_payload['exp'])
    

class AgentClient:
    def __init__(self):
        # 1. 补充缺失的资源组参数初始化
        self.tenant_id = os.getenv("TENANT_ID")
        self.client_id = os.getenv("CLIENT_ID")
        self.client_secret = os.getenv("CLIENT_SECRET")
        self.foundry_account_name = os.getenv("FOUNDRY_ACCOUNT_NAME")
        self.foundry_project_name = os.getenv("FOUNDRY_PROJECT_NAME")
        self.foundry_resource_group = os.getenv("FOUNDRY_RESOURCE_GROUP")  # 新增：初始化资源组
        self.agent_identity_blueprint_id = os.getenv("AGENT_IDENTITY_BLUEPRINT_ID")
        self.agent_name = os.getenv("AGENT_NAME")  # azure-ai-projects 2.x 使用 agent_name 而非 agent_id
        
        # 2. 初始化时仅声明，不创建Client（避免credential为空）
        self.credential = None
        self.client = None
    
    def _create_client(self) -> AIProjectClient:
        """创建/更新Agent Service Client实例（使用最新的credential）"""
        if not self.credential:
            raise ValueError("请先调用set_obo_credential/set_client_credential设置有效凭据")
        
        endpoint = f"https://{self.foundry_account_name}.services.ai.azure.com/api/projects/{self.foundry_project_name}"
        # 3. 创建有效Client（传入最新的credential）
        
        client = AIProjectClient(endpoint=endpoint, credential=self.credential, api_version="v1", allow_preview=True)
        self.client = client  # 关键：赋值给self.client
        return client
    
    def set_static_sts_token(self, user_token: str):
        """设置用户的access token"""
        self.credential = StaticTokenCredential(user_token)
        self._create_client()

    def set_obo_credential(self, user_token: str):
        """设置OBO凭据并立即更新Client"""
        self.credential = OnBehalfOfCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
            user_assertion=user_token
        )
        print(user_token)
        self._create_client()  # 设置凭据后立即创建有效Client
    
    def set_client_credential(self):
        """设置客户端凭据并立即更新Client"""
        self.credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret
        )
        self._create_client()  # 设置凭据后立即创建有效Client
    
    def get_agent_status(self) -> dict:
        """获取Agent状态信息（适配 azure-ai-projects 2.x SDK）"""
        # 确保Client已创建（兜底校验）
        if not self.client:
            self._create_client()
        
        if not self.client:
            raise ValueError("failed to create agent client: client is None")
        
        if not self.agent_name:
            raise ValueError("failed to get agent status: AGENT_NAME is not set")
        
        # azure-ai-projects 2.x: 使用 get(agent_name) 替代旧版的 get_agent(agent_id)
        agent_status = self.client.agents.get(agent_name=self.agent_name)
        print(f"Agent status: {agent_status.status}")
        return agent_status.as_dict() if hasattr(agent_status, 'as_dict') else dict(agent_status)
    
    def send_message_to_agent(self, message: str) -> str:
        """向Agent发送消息（使用 OpenAI Responses API，适配 azure-ai-projects 2.x SDK）
        
        根据 Azure Foundry 官方文档，2.x SDK 使用 openai.responses.create()
        配合 agent_reference 与 Agent 交互，不再使用 threads/messages/runs 模式。
        """
        if not self.client:
            self._create_client()
        
        if not self.agent_name:
            raise ValueError("AGENT_NAME is not set")
        
        # 获取 OpenAI client
        openai_client = self.client.get_openai_client(agent_name=self.agent_name)
        
        # 使用 Responses API 生成回复（配合 agent_reference）
        response = openai_client.responses.create(
            extra_body={
                "agent_reference": {
                    "name": self.agent_name,
                    "type": "agent_reference",
                }
            },
            input=message,
        )
        
        print(f"Response ID: {response.id}")
        print(f"Status: {response.status}")
        
        # 打印所有 output 项（包括工具调用和文本回复）
        if hasattr(response, 'output') and response.output:
            for item in response.output:
                item_type = getattr(item, 'type', 'unknown')
                if item_type == "message":
                    # 助手回复文本
                    content = item.content
                    if content:
                        text_val = content[0].text if hasattr(content[0], 'text') else str(content[0])
                        print(f"[Assistant] {text_val}")
                elif item_type == "web_search_call":
                    print(f"[Tool] Web search: status={getattr(item, 'status', 'unknown')}")
                elif item_type == "function_call":
                    print(f"[Tool] Function call: {getattr(item, 'name', '')}({getattr(item, 'arguments', '')})")
                elif item_type == "file_search_call":
                    print(f"[Tool] File search: status={getattr(item, 'status', 'unknown')}")
        
        # 返回文本输出
        output_text = getattr(response, 'output_text', None)
        if output_text:
            return output_text
        
        # 兜底：从 output 中提取文本
        if hasattr(response, 'output') and response.output:
            for item in response.output:
                if getattr(item, 'type', None) == "message":
                    content = item.content
                    if content:
                        text_val = content[0].text if hasattr(content[0], 'text') else str(content[0])
                        return text_val
        
        return ""
    
    def create_agent(self, agent_name: str, description: str) -> dict:
        """创建新的Agent（适配 azure-ai-projects 2.x SDK）"""
        if not self.client:
            self._create_client()
        
        # azure-ai-projects 2.x: 使用 create_version 替代旧版的 create_or_update_agent
        # blueprint_reference 对应旧版的 identity_blueprint_id
        agent = self.client.agents.create_version(
            agent_name=agent_name,
            description=description,
            blueprint_reference=self.agent_identity_blueprint_id,
        )
        return agent.as_dict() if hasattr(agent, 'as_dict') else dict(agent)
    
if __name__ == "__main__":
    sts_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6InNNMV95QXhWOEdWNHlOLUI2ajJ4em1pazVBbyIsImtpZCI6InNNMV95QXhWOEdWNHlOLUI2ajJ4em1pazVBbyJ9.eyJhdWQiOiJodHRwczovL2FpLmF6dXJlLmNvbSIsImlzcyI6Imh0dHBzOi8vc3RzLndpbmRvd3MubmV0LzY1YTYzYWQwLTMzNmMtNDYxNi1iZDFmLTBjYmE5OWY0MzA3Ny8iLCJpYXQiOjE3NzE3MjcxOTMsIm5iZiI6MTc3MTcyNzE5MywiZXhwIjoxNzcxNzMyMjc1LCJhY3IiOiIxIiwiYWlvIjoiQWNRQU8vOGJBQUFBVGZuOEFRZjNubzUxcmdJVzh6WjZvQ05odFZDM29aWDBZZy9MUENWSHVHblFuWTN1cXpFR2NUZGQ5R2dYQWRvS0tlYnByRXRLRUJzUmJ2cGZKdkhYc2w2WUJkbmZYMi9DUXZONXAvOTRSR1crSWIrR1JkWHJ5Mm1GZzBaSkNWRVY0ZnFrYTB2eS93U2tycHppZlk3ZlRtc1B3TmZjVUdWSk9rcldsZ2RxbzhUdjhadFRXVEFobXZEU2hOUTB0cDhHSWNsYm9rSjEyMlUxNDRZd3lFSVpDdE5kZEdhNXN4aG85T3NrUXpHNFhIN0R5WU9sQ0V1ZnRhaUVsVXZzYUpxMCIsImFsdHNlY2lkIjoiMTpsaXZlLmNvbTowMDA2N0ZGRTcwRDE2ODNBIiwiYW1yIjpbInB3ZCIsIm1mYSJdLCJhcHBpZCI6IjczZmNiMGY2LTMzMGYtNGY0ZS1iMDAyLWY5MjFhZTA3ZDBjZSIsImFwcGlkYWNyIjoiMSIsImVtYWlsIjoiZmVyaWFreXVuZXN4a21xQGhvdG1haWwuY29tIiwiZmFtaWx5X25hbWUiOiJ5dW5lcyIsImdpdmVuX25hbWUiOiJmZXJpYSIsImdyb3VwcyI6WyI4YmUwYjdiNC1mOTk4LTRkOGItYThlMS03MzJjM2FkNzBjNzYiXSwiaWRwIjoibGl2ZS5jb20iLCJpZHR5cCI6InVzZXIiLCJpcGFkZHIiOiIzNi4xNjMuMTU4LjIzNCIsIm5hbWUiOiJmZXJpYSB5dW5lcyIsIm9pZCI6ImExYWM2OWViLWRlYmEtNDk2Zi1hMjkyLTgyOTk1MmFhNTI5MCIsInB1aWQiOiIxMDAzMjAwNThERTI0MUY0IiwicmgiOiIxLkFWSUEwRHFtWld3ekZrYTlId3k2bWZRd2QxOXZwaGpmMnhkTW5kY1dOSEVxbkw0QUFNWlNBQS4iLCJzY3AiOiJ1c2VyX2ltcGVyc29uYXRpb24iLCJzaWQiOiIwMDIxYzBiYS1jNWM3LTczYjMtYjhkMy05ZDUxYmNmNTY2MjIiLCJzdWIiOiJsam9EeElJU28zU0hYdU41U3R1QVBGU2ZHYnNCMkFuWFN1aEZvMXhoVHFRIiwidGlkIjoiNjVhNjNhZDAtMzM2Yy00NjE2LWJkMWYtMGNiYTk5ZjQzMDc3IiwidW5pcXVlX25hbWUiOiJsaXZlLmNvbSNmZXJpYWt5dW5lc3hrbXFAaG90bWFpbC5jb20iLCJ1dGkiOiJ6bUY5Q2lBV05VU3p5djJoTTRaeEFBIiwidmVyIjoiMS4wIiwieG1zX2FjdF9mY3QiOiIzIDkiLCJ4bXNfZnRkIjoiV3kxSjRFRzNsYkY2SWtqTXpOOF9ZNEx4RFp2NTJLMFRmdVFWcnduWmM5MEJkWE51YjNKMGFDMWtjMjF6IiwieG1zX2lkcmVsIjoiMSA4IiwieG1zX3N1Yl9mY3QiOiIzIDgifQ.eHtZZSUvV2TIQNoBv2hZ_z1IGjQLvYFbUnnQM9wqQXgCNkZ0HFrkZu7qfJ3g3Krvz2W3nGN40lxhz_Xw0e80mKZULq4TSMH-JDhdmbeIj3ihzTKAyWZSGBgVIaS44FD0PSIQ3413EHnpeTKhpTUXpeZCZVbx9hQp32ScD99XLY2H2KC5pwZLi7heomdUUoLQp_u_gT5ABzUytz5w_78l1tLu3A-vmNeaOkgpEWr57DuqoyBARzYulWqRI8JlSdiAlmefs3BfN5ygHHJS_HBFMg8cJgMuf-3MgkVXjl4qL3xFF63-Xy6VyOahU7M4uKU8Swh6KC3xYXyeACgxFfiNVQ"
    token_credential = StaticTokenCredential(sts_token)
    print(token_credential.get_token())
