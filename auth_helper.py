from msal import ConfidentialClientApplication
from typing import Optional
import os
from flask import session
from dotenv import load_dotenv  # 导入dotenv库

# 关键步骤：加载.env文件（必须放在读取环境变量之前）
load_dotenv()  # 自动查找项目根目录的.env文件

class AuthHelper:
    def __init__(self):
        self.tenant_id = os.getenv("TENANT_ID")
        self.client_id = os.getenv("CLIENT_ID")
        self.client_secret = os.getenv("CLIENT_SECRET")
        # 关键修复：scope建议用数组格式，且确保是Azure AI Foundry的正确scope
        self.scope = os.getenv("SCOPE", "https://ai.azure.com/.default").split(",")
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        # self.authority = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        
        self.app = ConfidentialClientApplication(
            client_id=self.client_id,
            authority=self.authority,
            client_credential=self.client_secret,
            token_cache=None
        )
    
    def get_user_token(self, username: str) -> Optional[str]:
        """尝试静默获取用户令牌"""
        print(f"require obo user access token with scope: {self.scope}")
        result = self.app.acquire_token_silent(
            scopes=[self.scope],
            account=self._find_account(username)
        )
        
        if not result:
            print("无法从缓存中获取令牌，需要交互式登录")
            result = self.interactive_login()
        
        return result.get("access_token") if result else None
    
    def _find_account(self, username: str) -> Optional[dict]:
        """在缓存中查找用户账户"""
        accounts = self.app.get_accounts(username=username)
        return next(iter(accounts), None) if accounts else None
    
    def interactive_login(self) -> str:  # 明确返回类型为str（auth_uri）
        """执行交互式登录，返回授权URL"""
        # 关键修复1：确保scope是数组
        scopes = self.scope if isinstance(self.scope, list) else [self.scope]
        # 关键修复2：重新初始化auth_code_flow，确保state新鲜
        auth_code_flow = self.app.initiate_auth_code_flow(
            scopes=scopes,
            redirect_uri=os.getenv("REDIRECT_URI")  # 关键：必须和Azure应用注册的回调URL一致
        )

        if "error" in auth_code_flow:
            print(f"❌ 生成授权流失败：{auth_code_flow['error']} - {auth_code_flow['error_description']}")
            raise Exception(f"生成授权URL失败：{auth_code_flow['error_description']}")

        # 关键修复3：强制存储flow到session（确保是flask的session）
        session["auth_code_flow"] = auth_code_flow
        print(f"✅ 生成授权URL成功，state={auth_code_flow['state']}")
        
        return auth_code_flow["auth_uri"]  # 仅返回授权URL，而非dict
        
    
    def get_client_credential_token(self) -> str:
        """获取客户端凭据令牌（用于服务器到服务器场景）"""
        result = self.app.acquire_token_for_client(scopes=[self.scope])
        return result.get("access_token") if result else None
    