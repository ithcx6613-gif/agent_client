import os
import base64
import json
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class AgentClient:
    """Client for calling Azure AI Foundry Agent via its OpenAI-compatible endpoint."""

    def __init__(self):
        self.foundry_account_name = os.getenv("FOUNDRY_ACCOUNT_NAME")
        self.foundry_project_name = os.getenv("FOUNDRY_PROJECT_NAME")
        self.agent_name = os.getenv("AGENT_NAME")
        self._user_token = None

    def set_token(self, user_token: str) -> None:
        self._user_token = user_token

    def check_agent_exists(self) -> dict:
        """Check connectivity by constructing the expected endpoint URL."""
        base = self._project_base_url()
        return {
            "agent_name": self.agent_name,
            "status": "configured",
            "project_endpoint": base,
        }

    # ------------------------------------------------------------------
    # Public: send a message and get the reply
    # ------------------------------------------------------------------
    def send_message(self, message: str) -> str:
        if not self._user_token:
            raise ValueError("Not authenticated")
        if not self.agent_name:
            raise ValueError("AGENT_NAME not set")

        versions = self._versions()
        errors = []

        def _try(base_url: str, use_agent_ref: bool):
            """Try ``responses.create()`` against *base_url*."""
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
                    body = self._error_detail(e)
                    errors.append(f"  base={base_url[:70]} v={v}: {body[:200]}")
                    continue
            return None

        # ---- Strategy A : project-level + agent_reference ----
        resp = _try(self._project_base_url(), use_agent_ref=True)
        if resp:
            return self._extract(resp)

        # ---- Strategy B : project-level, no reference ----
        resp = _try(self._project_base_url(), use_agent_ref=False)
        if resp:
            return self._extract(resp)

        # ---- Strategy C : agent-specific endpoint ----
        resp = _try(self._agent_base_url(), use_agent_ref=False)
        if resp:
            return self._extract(resp)

        raise RuntimeError("All strategies failed.\n" + "\n".join(errors))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _project_base_url(self) -> str:
        return (
            f"https://{self.foundry_account_name}.services.ai.azure.com"
            f"/api/projects/{self.foundry_project_name}/openai/v1"
        )

    def _agent_base_url(self) -> str:
        return (
            f"https://{self.foundry_account_name}.services.ai.azure.com"
            f"/api/projects/{self.foundry_project_name}"
            f"/agents/{self.agent_name}/endpoint/protocols/openai"
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
        return body

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
                        return content[0].text if hasattr(content[0], "text") else str(content[0])
        return ""
