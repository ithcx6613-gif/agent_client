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
        self._latest_version: Optional[str] = None

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
            "agent_version": self._versions()[0],
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

    def _versions(self) -> list:
        """Return a list of version strings to try, best-first.

        If AGENT_VERSION is explicitly set in the environment, use it directly.
        Otherwise, query Azure AI Foundry for the actual latest version of the
        configured agent.  Results are cached on the instance.
        """
        v = os.getenv("AGENT_VERSION")
        if v:
            return [v, "latest"]

        if self._latest_version is None:
            self._latest_version = self._fetch_latest_version()
        v = self._latest_version
        versions = [v]
        if v != "latest":
            versions.append("latest")
        return versions

    def _fetch_latest_version(self) -> str:
        """Query the latest active version of the agent via the SDK.

        Filters for active versions only — versions in creating/failed/deleted
        status are skipped.  Results are not paginated beyond the first page
        since most agents have few active versions.
        """
        if not self._project_client or not self.agent_name:
            print("[AgentClient] No project client or agent name — falling back to 'latest'")
            return "latest"
        try:
            versions = list(
                self._project_client.agents.list_versions(
                    agent_name=self.agent_name,
                    order="desc",
                    limit=50,
                )
            )
            for v in versions:
                if getattr(v, "status", None) == "active":
                    print(f"[AgentClient] Resolved latest active version: {v.version}")
                    return v.version
            print("[AgentClient] No active version found — falling back to 'latest'")
        except Exception as e:
            print(f"[AgentClient] Failed to fetch latest version: {e}")
        return "latest"

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
        """Extract text from a Responses API response object.

        Tries several strategies:
        1. The response.output_text convenience property (only matches
           content.type == "output_text").
        2. Manual iteration of all output items handles non-standard
           content types (e.g. Azure AI Foundry may return type="text"
           instead of "output_text").
        3. Debug dump when nothing yields text, so we can see the actual
           response structure.
        """
        # --- Strategy 1: use the SDK output_text property
        try:
            text = response.output_text
            if text and text.strip():
                return text
        except Exception:
            pass

        # --- Strategy 2: manually walk output -> message -> content -> text
        if hasattr(response, "output") and response.output:
            texts = []
            for item in response.output:
                item_type = getattr(item, "type", None) or "?"
                if item_type == "message":
                    content = getattr(item, "content", None) or []
                    for cp in content:
                        t = getattr(cp, "text", None)
                        if t and isinstance(t, str):
                            texts.append(t)

                # Also try reasoning items which sometimes carry text
                if item_type == "reasoning":
                    content = getattr(item, "content", None) or []
                    for cp in content:
                        t = getattr(cp, "text", None)
                        if t and isinstance(t, str):
                            texts.append(t)

            if texts:
                return "".join(texts)

        # --- Debug dump -- log the actual response structure
        print("[AgentClient] _extract: no text found -- response structure:")
        print(f"  type= {type(response).__name__}")
        print(f'  status= {getattr(response, "status", None)!r}')
        print(f'  err= {getattr(response, "error", None)!r}')
        output = getattr(response, "output", None)
        print(f"  output= {type(output).__name__} len={len(output) if output else 0}")
        if output:
            for i, item in enumerate(output):
                it = getattr(item, "type", None) or "?"
                print(f"    [{i}] type={it!r} item_type={type(item).__name__}")
                if hasattr(item, "content"):
                    for j, cp in enumerate(item.content):
                        ct = getattr(cp, "type", None) or "?"
                        print(f"        content[{j}] type={ct!r} cp_type={type(cp).__name__}")
                        for field in ("text", "refusal", "output"):
                            v = getattr(cp, field, None)
                            if v is not None:
                                print(f"          {field}={repr(v)[:200]}")
        return ""
