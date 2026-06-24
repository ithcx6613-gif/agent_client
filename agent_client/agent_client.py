"""AgentClient — uses AIProjectClient for project connectivity
and a direct OpenAI client (with the raw JWT as api_key) for data-plane
agent calls, because the SDK's get_openai_client() has a bearer-token
provider incompatibility that returns 404 on every request.
"""
import os
import base64
import json
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI
from azure.core.credentials import AccessToken, TokenCredential
from azure.ai.projects import AIProjectClient

load_dotenv()


# ---------------------------------------------------------------------------
# Custom credential that wraps a pre-acquired JWT
# ---------------------------------------------------------------------------
class StaticTokenCredential(TokenCredential):
    """Holds a user's OAuth access token and returns it for any scope."""

    def __init__(self, user_token: str):
        self.user_token = user_token

    def _decode_jwt(self) -> Optional[dict]:
        try:
            _, payload_b64, _ = self.user_token.split(".")
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            return json.loads(base64.urlsafe_b64decode(payload_b64))
        except Exception as e:
            print(f"[StaticTokenCredential] JWT decode failed: {e}")
            return None

    def get_token(self, *scopes, **kwargs) -> AccessToken:
        payload = self._decode_jwt()
        exp = (payload or {}).get("exp", 0)
        user = (payload or {}).get("name", "unknown")
        print(f"[StaticTokenCredential] user={user}, scopes={scopes}")
        return AccessToken(self.user_token, exp)


# ---------------------------------------------------------------------------
# AgentClient
# ---------------------------------------------------------------------------
class AgentClient:
    """Wraps AIProjectClient for configuration & verification, but uses a
    direct OpenAI constructor for the actual agent API calls."""

    def __init__(self):
        self.foundry_account_name = os.getenv("FOUNDRY_ACCOUNT_NAME")
        self.foundry_project_name = os.getenv("FOUNDRY_PROJECT_NAME")
        self.agent_name = os.getenv("AGENT_NAME")
        self._user_token = None
        self._project_client: Optional[AIProjectClient] = None

    def set_token(self, user_token: str) -> None:
        self._user_token = user_token
        # Create the AIProjectClient (used for configuration / verification)
        cred = StaticTokenCredential(user_token)
        endpoint = (
            f"https://{self.foundry_account_name}.services.ai.azure.com"
            f"/api/projects/{self.foundry_project_name}"
        )
        self._project_client = AIProjectClient(
            endpoint=endpoint,
            credential=cred,
            api_version="v1",
            allow_preview=True,
        )
        print(f"[AgentClient] AIProjectClient created: endpoint={endpoint}")

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def check_agent_exists(self) -> dict:
        return {
            "agent_name": self.agent_name or "N/A",
            "agent_version": os.getenv("AGENT_VERSION", "latest"),
            "project_endpoint": self._project_url("openai/v1"),
            "agent_endpoint": self._agent_url(),
        }

    # ------------------------------------------------------------------
    # Send message
    # ------------------------------------------------------------------
    def send_message(self, message: str) -> str:
        if not self._user_token:
            raise ValueError("Not authenticated")
        if not self.agent_name:
            raise ValueError("AGENT_NAME not set")

        versions = self._versions()
        errors = []

        def _try(base_url: str, use_agent_ref: bool) -> Optional[object]:
            print(f"\n  -> {base_url}")
            client = OpenAI(api_key=self._user_token, base_url=base_url)
            for v in versions:
                try:
                    extra = (
                        {
                            "agent_reference": {
                                "name": self.agent_name,
                                "version": v,
                                "type": "agent_reference",
                            }
                        }
                        if use_agent_ref
                        else None
                    )
                    resp = client.responses.create(input=message, extra_body=extra)
                    print(f"  <- OK  version={v}")
                    return resp
                except Exception as e:
                    errors.append(
                        f"  base={base_url[:70]} v={v}: {self._error_detail(e)}"
                    )
                    continue
            return None

        # Strategy A : project-level + agent_reference (the one that works)
        resp = _try(self._project_url("openai/v1"), use_agent_ref=True)
        if resp:
            return self._extract(resp)

        # Strategy B : project-level, no reference
        resp = _try(self._project_url("openai/v1"), use_agent_ref=False)
        if resp:
            return self._extract(resp)

        # Strategy C : agent-specific endpoint
        resp = _try(self._agent_url(), use_agent_ref=False)
        if resp:
            return self._extract(resp)

        raise RuntimeError("All strategies failed.\n" + "\n".join(errors))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _project_url(self, suffix: str = "") -> str:
        base = (
            f"https://{self.foundry_account_name}.services.ai.azure.com"
            f"/api/projects/{self.foundry_project_name}"
        )
        return f"{base}/{suffix}" if suffix else base

    def _agent_url(self) -> str:
        return self._project_url(
            f"agents/{self.agent_name}/endpoint/protocols/openai"
        )

    @staticmethod
    def _versions() -> list:
        v = os.getenv("AGENT_VERSION", "latest")
        versions = [v]
        if v != "latest":
            versions.append("latest")
        return versions

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        body = str(exc)
        if hasattr(exc, "response") and exc.response is not None:
            try:
                body = exc.response.text()
            except Exception:
                try:
                    body = str(exc.response)
                except Exception:
                    pass
        return body[:200]

    @staticmethod
    def _extract(response) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text
        if hasattr(response, "output") and response.output:
            for item in response.output:
                if getattr(item, "type", None) == "message":
                    content = item.content
                    if content:
                        return (
                            content[0].text
                            if hasattr(content[0], "text")
                            else str(content[0])
                        )
        return ""
