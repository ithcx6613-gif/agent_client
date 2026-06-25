from msal import ConfidentialClientApplication
from typing import Optional
import os
from flask import session
from dotenv import load_dotenv

load_dotenv()

# Server-side store for OAuth code flows, keyed by state.
# The full auth_code_flow dict is too large to fit in a Flask session cookie
# (the auth_uri alone can exceed 2 KB, pushing past browser cookie limits).
# Instead, we store the state in the session cookie and the full flow here.
_auth_flow_store: dict[str, dict] = {}


class AuthHelper:
    """Manage OAuth 2.0 auth-code flow against Microsoft Entra ID."""

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

        The full auth_code_flow dict is stored server-side (keyed by state)
        to avoid exceeding browser cookie size limits. Only the state is
        stored in the Flask session for CSRF protection.
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

        # Store the full flow server-side, keyed by state
        state = auth_code_flow["state"]
        _auth_flow_store[state] = auth_code_flow

        # Only store the state in the session cookie (small, fits in 4 KB)
        session["auth_flow_state"] = state
        session.permanent = True
        print(f"[AuthHelper] Flow initiated, state={state}")
        return auth_code_flow["auth_uri"]

    @staticmethod
    def get_flow_for_state(state: str) -> dict | None:
        """Retrieve and remove a stored auth code flow by state."""
        return _auth_flow_store.pop(state, None)
