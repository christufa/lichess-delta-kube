import asyncio

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from chess_mcp.auth import user_of


def verify(verifier, token):
    return asyncio.run(verifier.verify_token(token))


def test_assigned_user_gets_the_scope(verifier, make_token, settings):
    t = verify(verifier, make_token())
    assert settings.scope in t.scopes and t.subject.startswith("3333")
    assert user_of(t)["user"] == "owner@example.test"


def test_without_the_role_no_scope(verifier, make_token, settings):
    t = verify(verifier, make_token(roles=()))
    assert t is not None and settings.scope not in t.scopes  # authenticated, but 403 later


def test_without_the_delegated_scope_no_scope(verifier, make_token, settings):
    assert settings.scope not in verify(verifier, make_token(scp="User.Read")).scopes


@pytest.mark.parametrize("over", [
    {"iss": "https://login.microsoftonline.com/other-tenant/v2.0"},
    {"aud": "someone-else"},
    {"exp": 1},
])
def test_wrong_issuer_audience_or_expired(verifier, make_token, over):
    assert verify(verifier, make_token(**over)) is None


def test_app_id_uri_audience_accepted(verifier, make_token, settings):
    assert verify(verifier, make_token(aud=f"api://{settings.client_id}")) is not None


def test_signed_by_another_key(verifier, settings):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({"iss": settings.issuer, "aud": settings.client_id, "sub": "x", "iat": 1, "exp": 2**31},
                       other, algorithm="RS256")
    assert verify(verifier, token) is None


def test_garbage(verifier):
    assert verify(verifier, "not-a-jwt") is None


def test_kubernetes_service_variables_dont_break_settings(monkeypatch):
    # A Service named chess-mcp makes Kubernetes inject CHESS_MCP_PORT=tcp://…; it must be ignored.
    from chess_mcp.settings import Settings
    monkeypatch.setenv("CHESS_MCP_PORT", "tcp://10.0.0.1:80")
    monkeypatch.setenv("CHESS_MCP_SERVICE_PORT", "80")
    assert Settings().http_port == 8000
