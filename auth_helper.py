from msal import ConfidentialClientApplication
from typing import Optional
import os
from flask import session
from dotenv import load_dotenv

load_dotenv()


class AuthHelper:
    """Manage OAuth 2.0 auth-code flow against Microsoft Entra ID.

    The access token returned is scoped to https://ai.azure.com/.default
    and can be passed to AgentClient for Azure AI Foundry API calls.
    """

    def __init__(self):
        self.tenant_id = os.getenv("TENANT_ID")
        self.client_id = os.getenv("CLIENT_ID")
        self.client_secret = os.getenv("CLIENT_SECRET")
        self.scope = os.getenv("SCOPE", "https://ai.azure.com/.default").split(",")
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"

        self.app = ConfidentialClientApplication(
            client_id=self.client_id,
            authority=self.authority,
            client_credential=self.client_secret,
        )

    def interactive_login(self) -> str:
        """Initiate an auth-code flow and return the authorization URL.

        The flow state is persisted in the Flask session for CSRF protection
        and later exchanged in the callback handler.
        """
        scopes = self.scope if isinstance(self.scope, list) else [self.scope]
        auth_code_flow = self.app.initiate_auth_code_flow(
            scopes=scopes,
            redirect_uri=os.getenv("REDIRECT_URI"),
        )

        if "error" in auth_code_flow:
            raise RuntimeError(
                f"Auth flow init failed: "
                f"{auth_code_flow['error']} - {auth_code_flow.get('error_description', '')}"
            )

        session["auth_code_flow"] = auth_code_flow
        session.permanent = True
        print(f"[AuthHelper] Flow initiated, state={auth_code_flow['state']}")
        return auth_code_flow["auth_uri"]
