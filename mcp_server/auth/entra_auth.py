"""Microsoft Entra token validation for MCP server.

Validates Bearer tokens issued by Microsoft Entra for
Agent Identity or Project Managed Identity authentication.

Uses JWKS (JSON Web Key Set) discovery to verify token signatures.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import jwt
from jwt import PyJWKSet
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from mcp_server.config import cfg

logger = logging.getLogger("mcp-server.auth")

# ---------------------------------------------------------------------------
# JWKS cache
# ---------------------------------------------------------------------------

_jwks_cache: dict[str, Any] = {"set": None, "expires_at": 0}
_CACHE_TTL = 3600


def _get_jwks(force_refresh: bool = False) -> PyJWKSet:
    now = time.time()
    if not force_refresh and _jwks_cache["set"] is not None and _jwks_cache["expires_at"] > now:
        return _jwks_cache["set"]

    jwks_uri = f"https://{cfg.authority_host}/{cfg.tenant_id}/discovery/v2.0/keys"
    logger.info("Fetching JWKS from %s", jwks_uri)

    resp = httpx.get(jwks_uri, timeout=10)
    resp.raise_for_status()
    key_set = PyJWKSet.from_dict(resp.json())

    _jwks_cache["set"] = key_set
    _jwks_cache["expires_at"] = now + _CACHE_TTL
    return key_set


# ---------------------------------------------------------------------------
# Token validation
# ---------------------------------------------------------------------------


class EntraTokenValidator:
    """Validates Microsoft Entra OAuth 2.0 / OIDC tokens.

    Verifies:
      - Signature via JWKS (RS256 / RS384 / RS512)
      - Issuer matches ``https://{host}/{tenant}/v2.0``
      - Audience matches the configured app / client ID
      - Token has not expired
    """

    def __init__(
        self,
        tenant_id: str,
        expected_audience: str,
        authority_host: str = "login.microsoftonline.com",
    ) -> None:
        self.tenant_id = tenant_id
        self.expected_audience = expected_audience
        self.authority_host = authority_host
        self.issuer = f"https://{authority_host}/{tenant_id}/v2.0"

    def validate(self, token: str) -> dict[str, Any]:
        jwks = _get_jwks()
        for attempt in range(2):
            try:
                return jwt.decode(
                    token,
                    key=jwks,
                    algorithms=["RS256", "RS384", "RS512"],
                    audience=self.expected_audience,
                    issuer=self.issuer,
                    options={
                        "verify_aud": True,
                        "verify_exp": True,
                        "verify_signature": True,
                    },
                )
            except (jwt.ExpiredSignatureError, jwt.InvalidAudienceError, jwt.InvalidIssuerError):
                raise
            except jwt.PyJWTError:
                if attempt == 0:
                    logger.info("JWKS verification failed, refreshing keys...")
                    jwks = _get_jwks(force_refresh=True)
                    continue
                raise jwt.InvalidTokenError("Could not validate token with any JWKS key")

        raise jwt.InvalidTokenError("Could not validate token")


# ---------------------------------------------------------------------------
# Starlette middleware
# ---------------------------------------------------------------------------


class EntraAuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that validates incoming Bearer tokens.

    Allows unauthenticated access only when ``cfg.auth_enabled`` is ``False``
    (e.g. local development). Also allows ``/health`` through for Azure
    Functions / load balancer health probes.
    """

    # Paths that should always bypass auth
    _PUBLIC_PATHS = frozenset({"/health", "/favicon.ico"})

    def __init__(
        self,
        app: ASGIApp,
        tenant_id: str | None = None,
        expected_audience: str | None = None,
    ) -> None:
        super().__init__(app)
        self._tenant_id = tenant_id or cfg.tenant_id
        self._audience = expected_audience or cfg.auth_audience
        self._validator = EntraTokenValidator(
            tenant_id=self._tenant_id,
            expected_audience=self._audience,
            authority_host=cfg.authority_host,
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> JSONResponse:
        # Allow public paths through without auth
        if request.url.path in self._PUBLIC_PATHS:
            return await call_next(request)

        if not cfg.auth_enabled:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning("Missing or malformed Authorization header")
            return JSONResponse(
                {"error": "Unauthorized", "detail": "Missing or invalid Bearer token"},
                status_code=401,
            )

        token = auth_header.removeprefix("Bearer ")
        try:
            claims = self._validator.validate(token)
            request.state.auth_claims = claims
            request.state.agent_identity_id = claims.get("sub", "")
            logger.info(
                "Authenticated agent identity: %s (tid=%s)",
                claims.get("sub", "?"),
                claims.get("tid", "?"),
            )
        except jwt.PyJWTError as exc:
            logger.warning("Token validation failed: %s", exc)
            return JSONResponse(
                {"error": "Unauthorized", "detail": str(exc)},
                status_code=401,
            )

        return await call_next(request)
