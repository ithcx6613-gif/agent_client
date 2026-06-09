from azure.identity import OnBehalfOfCredential, ClientSecretCredential
from azure.core.credentials import AccessToken
from azure.ai.projects import AIProjectClient
from typing import Optional
import os
from dotenv import load_dotenv
import base64
import json
import time
from datetime import datetime

load_dotenv()


class StaticTokenCredential:
    def __init__(self, user_token: str):
        self.user_token = user_token

    def decode_jwt(self):
        try:
            header_b64, payload_b64, signature = self.user_token.split('.')

            def b64url_decode(s):
                s += '=' * (4 - len(s) % 4)
                return base64.urlsafe_b64decode(s)

            header = json.loads(b64url_decode(header_b64))
            payload = json.loads(b64url_decode(payload_b64))

            for key in ['iat', 'nbf', 'exp']:
                if key in payload:
                    payload[f'{key}_cn'] = datetime.fromtimestamp(payload[key] + 8*3600).strftime('%Y-%m-%d %H:%M:%S')

            return {
                'header': header,
                'payload': payload,
                'signature': signature
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
        self.tenant_id = os.getenv("TENANT_ID")
        self.client_id = os.getenv("CLIENT_ID")
        self.client_secret = os.getenv("CLIENT_SECRET")
        self.foundry_account_name = os.getenv("FOUNDRY_ACCOUNT_NAME")
        self.foundry_project_name = os.getenv("FOUNDRY_PROJECT_NAME")
        self.foundry_resource_group = os.getenv("FOUNDRY_RESOURCE_GROUP")
        self.agent_identity_blueprint_id = os.getenv("AGENT_IDENTITY_BLUEPRINT_ID")
        self.agent_name = os.getenv("AGENT_NAME")  # Agent 名称（不是 ID）
        self.agent_version = os.getenv("AGENT_VERSION", "1")  # Agent 版本号

        self.credential = None
        self.client = None

    def _create_client(self) -> AIProjectClient:
        """创建 AIProjectClient 实例"""
        if not self.credential:
            raise ValueError("请先调用 set_obo_credential / set_client_credential 设置有效凭据")

        endpoint = f"https://{self.foundry_account_name}.services.ai.azure.com/api/projects/{self.foundry_project_name}"
        client = AIProjectClient(endpoint=endpoint, credential=self.credential, allow_preview=True)
        self.client = client
        return client

    def _get_openai_client(self):
        """获取 OpenAI 兼容客户端，用于调用 Agent"""
        if not self.client:
            self._create_client()

        if not self.agent_name:
            raise ValueError("请设置 AGENT_NAME 环境变量")

        openai_client = self.client.get_openai_client(agent_name=self.agent_name)
        return openai_client

    def set_static_sts_token(self, user_token: str):
        """设置用户的 access token"""
        self.credential = StaticTokenCredential(user_token)
        self._create_client()

    def set_obo_credential(self, user_token: str):
        """设置 OBO 凭据并立即更新 Client"""
        self.credential = OnBehalfOfCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
            user_assertion=user_token
        )
        print(user_token)
        self._create_client()

    def set_client_credential(self):
        """设置客户端凭据并立即更新 Client"""
        self.credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret
        )
        self._create_client()

    def get_agent_status(self) -> dict:
        """获取 Agent 状态信息"""
        if not self.client:
            self._create_client()

        if not self.client:
            raise ValueError("failed to create agent client: client is None")

        if not self.agent_name:
            raise ValueError(f"failed to get agent status: agent name is {self.agent_name}")

        agent_ops = self.client.agents
        agent_status = agent_ops.get(self.agent_name)
        return agent_status.as_dict() if hasattr(agent_status, 'as_dict') else dict(agent_status)

    def send_message_to_agent(self, message: str) -> str:
        """向 Agent 发送消息并获取回复"""
        openai_client = self._get_openai_client()

        response = openai_client.responses.create(
            input=[{"role": "user", "content": message}],
            extra_body={
                "agent_reference": {
                    "name": self.agent_name,
                    "version": self.agent_version,
                    "type": "agent_reference"
                }
            }
        )

        output_text = response.output_text
        print(f"Response output: {output_text}")
        return output_text

    def create_agent(self, agent_name: str, description: str) -> dict:
        """创建新的 Agent"""
        if not self.client:
            self._create_client()

        agent_ops = self.client.agents
        return agent_ops.create_or_update_agent(
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
