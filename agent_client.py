from azure.identity import OnBehalfOfCredential, ClientSecretCredential
from azure.core.credentials import AccessToken
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import MessageRole
from azure.ai.agents.models import ListSortOrder
from typing import Optional
import os
from dotenv import load_dotenv
import base64
import json,time
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
        for scope in scopes:
            if token_payload['aud'] in scope:
                print(f"decode token payload, user = {token_payload['name']}, aud={token_payload['aud']}, expired at {token_payload['exp']}")
                return AccessToken(self.user_token, token_payload['exp'])
        raise ValueError("mismatch token scopes")
    

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
        self.agent_id = os.getenv("AGENT_ID")
        
        # 2. 初始化时仅声明，不创建Client（避免credential为空）
        self.credential = None
        self.client = None
    
    def _create_client(self) -> AIProjectClient:
        """创建/更新Agent Service Client实例（使用最新的credential）"""
        if not self.credential:
            raise ValueError("请先调用set_obo_credential/set_client_credential设置有效凭据")
        
        endpoint = f"https://{self.foundry_account_name}.services.ai.azure.com/api/projects/{self.foundry_project_name}?"
        # 3. 创建有效Client（传入最新的credential）
        
        client = AIProjectClient(endpoint=endpoint, credential=self.credential)
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
        """获取Agent状态信息（修正SDK方法名+校验Client）"""
        # 4. 确保Client已创建（兜底校验）
        if not self.client:
            self._create_client()
        
        if not self.client:
            raise ValueError("failed to create agent client: client is None")
        
        if not self.agent_id:
            raise ValueError(f"failed to get agent status: agent name is {self.agent_id}")            

        agent_ops = self.client.agents
        # 调用正确的get_agent方法，参数顺序/名称严格匹配SDK要求
        agent_status = agent_ops.get_agent(
            resource_group_name=self.foundry_resource_group,
            project_name=self.foundry_project_name,
            agent_id=self.agent_id
        )
        # 转换为字典返回（适配后端接口返回格式）
        return agent_status.as_dict() if hasattr(agent_status, 'as_dict') else dict(agent_status)
    
    def send_message_to_agent(self, message: str) -> dict:
        """向Agent发送消息（使用正确的SDK调用逻辑）"""
        if not self.client:
            self._create_client()

        agent_ops = self.client.agents
        # 获取Agent实例
        current_agent = agent_ops.get_agent(self.agent_id)
        print(f"Got agent: {current_agent.id}")
        
        # 创建会话线程
        thread = agent_ops.threads.create()
        print(f"Created thread: {thread.id}")
        
        # 发送用户消息
        msg = agent_ops.messages.create(
            thread_id=thread.id,
            role="user",
            content=message
        )
        print(f"Created message id={msg.id}, thread_id={msg.thread_id}")
        
        # 启动Agent运行
        run = agent_ops.runs.create(
            thread_id=thread.id,
            agent_id=current_agent.id
        )
        print(f"Started run: {run.id}")
        
        # 等待运行完成
        while run.status in ["queued", "in_progress", "requires_action"]:
            time.sleep(1)
            run = agent_ops.runs.get(thread_id=thread.id, run_id=run.id)
            print(f"Run status: {run.status}")
        
        print(f"Run completed with status: {run.status}")
        
        # 获取所有消息
        messages = agent_ops.messages.list(
            thread_id=thread.id,
            order=ListSortOrder.ASCENDING
        )
        
        # 提取最后一条助手回复
        assistant_messages = [m for m in messages if m.role == "assistant"]
        if assistant_messages:
            last_msg = assistant_messages[-1]
            if last_msg.text_messages:
                output_text = last_msg.text_messages[-1].text.value
                print(f"Response output: {output_text}")
                return output_text
        
        return ""
    

    def create_agent(self, agent_name: str, description: str) -> dict:
        """创建新的Agent（修正方法名+参数）"""
        if not self.client:
            self._create_client()
        
        agent_ops = self.client.agents
        return agent_ops.create_or_update_agent(  # 修正方法名：create_or_update → create_or_update_agent
            resource_group_name=self.foundry_resource_group,
            project_name=self.foundry_project_name,
            agent_name=agent_name,
            identity_blueprint_id=self.agent_identity_blueprint_id,
            description=description
        ).as_dict()
    
if __name__ == "__main__":
    sts_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6InNNMV95QXhWOEdWNHlOLUI2ajJ4em1pazVBbyIsImtpZCI6InNNMV95QXhWOEdWNHlOLUI2ajJ4em1pazVBbyJ9.eyJhdWQiOiJodHRwczovL2FpLmF6dXJlLmNvbSIsImlzcyI6Imh0dHBzOi8vc3RzLndpbmRvd3MubmV0LzY1YTYzYWQwLTMzNmMtNDYxNi1iZDFmLTBjYmE5OWY0MzA3Ny8iLCJpYXQiOjE3NzE3MjcxOTMsIm5iZiI6MTc3MTcyNzE5MywiZXhwIjoxNzcxNzMyMjc1LCJhY3IiOiIxIiwiYWlvIjoiQWNRQU8vOGJBQUFBVGZuOEFRZjNubzUxcmdJVzh6WjZvQ05odFZDM29aWDBZZy9MUENWSHVHblFuWTN1cXpFR2NUZGQ5R2dYQWRvS0tlYnByRXRLRUJzUmJ2cGZKdkhYc2w2WUJkbmZYMi9DUXZONXAvOTRSR1crSWIrR1JkWHJ5Mm1GZzBaSkNWRVY0ZnFrYTB2eS93U2tycHppZlk3ZlRtc1B3TmZjVUdWSk9rcldsZ2RxbzhUdjhadFRXVEFobXZEU2hOUTB0cDhHSWNsYm9rSjEyMlUxNDRZd3lFSVpDdE5kZEdhNXN4aG85T3NrUXpHNFhIN0R5WU9sQ0V1ZnRhaUVsVXZzYUpxMCIsImFsdHNlY2lkIjoiMTpsaXZlLmNvbTowMDA2N0ZGRTcwRDE2ODNBIiwiYW1yIjpbInB3ZCIsIm1mYSJdLCJhcHBpZCI6IjczZmNiMGY2LTMzMGYtNGY0ZS1iMDAyLWY5MjFhZTA3ZDBjZSIsImFwcGlkYWNyIjoiMSIsImVtYWlsIjoiZmVyaWFreXVuZXN4a21xQGhvdG1haWwuY29tIiwiZmFtaWx5X25hbWUiOiJ5dW5lcyIsImdpdmVuX25hbWUiOiJmZXJpYSIsImdyb3VwcyI6WyI4YmUwYjdiNC1mOTk4LTRkOGItYThlMS03MzJjM2FkNzBjNzYiXSwiaWRwIjoibGl2ZS5jb20iLCJpZHR5cCI6InVzZXIiLCJpcGFkZHIiOiIzNi4xNjMuMTU4LjIzNCIsIm5hbWUiOiJmZXJpYSB5dW5lcyIsIm9pZCI6ImExYWM2OWViLWRlYmEtNDk2Zi1hMjkyLTgyOTk1MmFhNTI5MCIsInB1aWQiOiIxMDAzMjAwNThERTI0MUY0IiwicmgiOiIxLkFWSUEwRHFtWld3ekZrYTlId3k2bWZRd2QxOXZwaGpmMnhkTW5kY1dOSEVxbkw0QUFNWlNBQS4iLCJzY3AiOiJ1c2VyX2ltcGVyc29uYXRpb24iLCJzaWQiOiIwMDIxYzBiYS1jNWM3LTczYjMtYjhkMy05ZDUxYmNmNTY2MjIiLCJzdWIiOiJsam9EeElJU28zU0hYdU41U3R1QVBGU2ZHYnNCMkFuWFN1aEZvMXhoVHFRIiwidGlkIjoiNjVhNjNhZDAtMzM2Yy00NjE2LWJkMWYtMGNiYTk5ZjQzMDc3IiwidW5pcXVlX25hbWUiOiJsaXZlLmNvbSNmZXJpYWt5dW5lc3hrbXFAaG90bWFpbC5jb20iLCJ1dGkiOiJ6bUY5Q2lBV05VU3p5djJoTTRaeEFBIiwidmVyIjoiMS4wIiwieG1zX2FjdF9mY3QiOiIzIDkiLCJ4bXNfZnRkIjoiV3kxSjRFRzNsYkY2SWtqTXpOOF9ZNEx4RFp2NTJLMFRmdVFWcnduWmM5MEJkWE51YjNKMGFDMWtjMjF6IiwieG1zX2lkcmVsIjoiMSA4IiwieG1zX3N1Yl9mY3QiOiIzIDgifQ.eHtZZSUvV2TIQNoBv2hZ_z1IGjQLvYFbUnnQM9wqQXgCNkZ0HFrkZu7qfJ3g3Krvz2W3nGN40lxhz_Xw0e80mKZULq4TSMH-JDhdmbeIj3ihzTKAyWZSGBgVIaS44FD0PSIQ3413EHnpeTKhpTUXpeZCZVbx9hQp32ScD99XLY2H2KC5pwZLi7heomdUUoLQp_u_gT5ABzUytz5w_78l1tLu3A-vmNeaOkgpEWr57DuqoyBARzYulWqRI8JlSdiAlmefs3BfN5ygHHJS_HBFMg8cJgMuf-3MgkVXjl4qL3xFF63-Xy6VyOahU7M4uKU8Swh6KC3xYXyeACgxFfiNVQ"
    token_credential = StaticTokenCredential(sts_token)
    print(token_credential.get_token())
    