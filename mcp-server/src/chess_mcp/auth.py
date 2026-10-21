"""Entra ID access tokens for this resource server.

A token is accepted (signature from Entra's keys, issuer = the tenant's v2.0 issuer, audience =
this app) and mapped to an AccessToken. The scope this server requires (settings.scope, e.g.
api://<app>/MCP.Access) is granted only when the token has *both*:

  - the delegated scope `scp` MCP.Access: the client asked for it, and
  - the app role `roles` Lakehouse.Query: the user is assigned to the app in Entra.

Without the role a valid token still authenticates but lacks the scope, so the SDK answers
403 insufficient_scope; an invalid or missing token gets 401 with the resource metadata URL.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import anyio
import jwt
from mcp.server.auth.provider import AccessToken

from .settings import Settings

log = logging.getLogger(__name__)


class EntraTokenVerifier:
    def __init__(self, settings: Settings, key_for: Callable[[str], Any] | None = None):
        self.s = settings
        if key_for is None:
            jwks = jwt.PyJWKClient(settings.jwks_url, cache_keys=True, lifespan=3600, timeout=10)
            key_for = lambda token: jwks.get_signing_key_from_jwt(token).key  # noqa: E731
        self._key_for = key_for
        self._audiences = [settings.client_id, settings.app_id_uri or f"api://{settings.client_id}"]

    def decode(self, token: str) -> dict[str, Any]:
        """Verify and decode (sync; raises jwt.PyJWTError). Separate for tests."""
        return jwt.decode(
            token,
            self._key_for(token),
            algorithms=["RS256"],
            audience=self._audiences,
            issuer=self.s.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            leeway=60,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(self.decode, token)
        except jwt.PyJWTError as e:
            log.info("token rejected", extra={"reason": f"{type(e).__name__}: {e}"})
            return None
        except Exception as e:  # key download etc.: treat as unauthenticated, but say why
            log.warning("token check failed", extra={"reason": f"{type(e).__name__}: {e}"})
            return None
        return self.to_access_token(token, claims)

    def to_access_token(self, token: str, claims: dict[str, Any]) -> AccessToken:
        delegated = set((claims.get("scp") or "").split())
        roles = set(claims.get("roles") or [])
        scopes = sorted(delegated | {f"role:{r}" for r in roles})
        if self.s.delegated_scope in delegated and self.s.required_role in roles:
            scopes.append(self.s.scope)
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("appid") or ""),
            scopes=scopes,
            expires_at=claims.get("exp"),
            subject=str(claims.get("oid") or claims.get("sub")),
            claims={k: claims.get(k) for k in ("iss", "tid", "oid", "name", "preferred_username", "roles", "scp")},
        )


def user_of(token: AccessToken | None) -> dict[str, str]:
    """Who is calling, for audit logs and rate limits."""
    if token is None:
        return {"oid": "anonymous", "user": "anonymous", "client": ""}
    c = token.claims or {}
    return {
        "oid": token.subject or "",
        "user": str(c.get("preferred_username") or c.get("name") or token.subject),
        "client": token.client_id,
    }
