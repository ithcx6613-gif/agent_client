"""On-Behalf-Of (OBO) token exchange helper.

In production (Azure Easy Auth + Managed Identity), exchanges the user's
access token for a downstream token (e.g. Microsoft Graph).

References:
  - remote-mcp-functions-python hello_tool_with_auth.py
  - https://learn.microsoft.com/azure/active-directory/develop/v2-oauth2-on-behalf-of-flow
"""

import base64
import json
import logging

logger = logging.getLogger("mcp-server.obo")


def decode_user_token(user_token: str) -> dict | None:
    """Decode a JWT access token to extract claims without validation.

    Useful for reading the user identity (name, email, tenant ID, etc.)
    from the token headers before performing the OBO exchange.

    Args:
        user_token: The user's OAuth access token (JWT format).

    Returns:
        Decoded payload dict, or None on failure.
    """
    try:
        parts = user_token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception as e:
        logger.warning("Failed to decode user token: %s", e)
        return None


def get_user_info_from_token(user_token: str) -> dict:
    """Extract user info from a JWT token without making API calls.

    Returns a dict with available claims (name, email, oid, tid, etc.)
    plus an 'error' key if decoding fails.
    """
    payload = decode_user_token(user_token)
    if not payload:
        return {"error": "Unable to decode token"}

    return {
        "name": payload.get("name", ""),
        "email": payload.get("email", "") or payload.get("upn", ""),
        "oid": payload.get("oid", ""),
        "tid": payload.get("tid", ""),
        "iss": payload.get("iss", ""),
        "exp": payload.get("exp", 0),
    }
