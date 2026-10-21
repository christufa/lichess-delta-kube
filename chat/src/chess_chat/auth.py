"""Two directions of Entra ID:

- In: who may chat. A bearer token for the "chess Chat" app, checked against Entra's signing keys
  (issuer = the tenant's v2.0 issuer, audience = the app), with delegated scope Chat.Access and
  the *assigned* app role Chat.User. No token: 401; token without the role: 403.
- Out: how the agent reaches the MCP server. This pod's workload identity (a projected service
  account token, federated with a managed identity) is exchanged for an app-only token for the
  MCP app; that identity holds the MCP app's role Lakehouse.Query.
"""
from __future__ import annotations

import os
import time
import urllib.parse
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any

import httpx2
import jwt

from .settings import Settings


@dataclass(frozen=True)
class User:
    oid: str
    name: str


class NotSignedIn(Exception):
    """No usable token (401)."""


class NotAllowed(Exception):
    """A valid token, but the user isn't assigned the chat role (403)."""


class UserTokenVerifier:
    def __init__(self, settings: Settings, key_for: Callable[[str], Any] | None = None):
        self.s = settings
        if key_for is None:
            jwks = jwt.PyJWKClient(settings.jwks_url, cache_keys=True, lifespan=3600, timeout=10)
            key_for = lambda token: jwks.get_signing_key_from_jwt(token).key  # noqa: E731
        self._key_for = key_for
        self._audiences = [settings.client_id, f"api://{settings.client_id}"]

    def verify(self, authorization: str | None) -> User:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise NotSignedIn("sign in to chat")
        try:
            claims = jwt.decode(token, self._key_for(token), algorithms=["RS256"], audience=self._audiences,
                                issuer=self.s.issuer, options={"require": ["exp", "iat", "iss", "aud", "sub"]},
                                leeway=60)
        except jwt.PyJWTError as e:
            raise NotSignedIn("sign-in expired or invalid; sign in again") from e
        scopes = set((claims.get("scp") or "").split())
        if self.s.delegated_scope not in scopes or self.s.required_role not in (claims.get("roles") or []):
            raise NotAllowed("your account isn't allowed to use the chat")
        return User(oid=str(claims.get("oid") or claims["sub"]),
                    name=str(claims.get("name") or claims.get("preferred_username") or "you"))


class WorkloadIdentityAuth(httpx2.Auth):
    """Bearer token for the MCP app, from the pod's workload identity (client credentials with a
    federated assertion; no SDK, no secret). Cached until five minutes before it expires."""

    def __init__(self, scope: str, env: dict[str, str] | None = None):
        self.scope = scope
        self.env = os.environ if env is None else env
        self._token, self._expires = "", 0.0

    def _fetch(self) -> httpx2.Request:
        with open(self.env["AZURE_FEDERATED_TOKEN_FILE"]) as fh:
            assertion = fh.read().strip()
        authority = self.env.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com/").rstrip("/")
        return httpx2.Request(
            "POST", f"{authority}/{self.env['AZURE_TENANT_ID']}/oauth2/v2.0/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            content=urllib.parse.urlencode({
                "client_id": self.env["AZURE_CLIENT_ID"],
                "scope": self.scope,
                "grant_type": "client_credentials",
                "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                "client_assertion": assertion,
            }),
        )

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response]:
        if time.monotonic() > self._expires:
            response = yield self._fetch()
            response.read()
            response.raise_for_status()
            body = response.json()
            self._token = body["access_token"]
            self._expires = time.monotonic() + int(body.get("expires_in", 3600)) - 300
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request
