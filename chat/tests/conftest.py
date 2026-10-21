import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from chess_chat.auth import UserTokenVerifier
from chess_chat.ledger import Ledger
from chess_chat.settings import Settings

TENANT = "11111111-1111-1111-1111-111111111111"
CHAT_APP = "55555555-5555-5555-5555-555555555555"
OWNER = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(scope="session")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def settings(tmp_path):
    return Settings(tenant_id=TENANT, client_id=CHAT_APP, mcp_client_id="mcp-app", daily_cap_usd=5.0,
                    user_daily_cap_usd=5.0, ledger_path=tmp_path / "spend.sqlite3", chats_per_minute=3)


@pytest.fixture
def ledger(settings):
    return Ledger(settings.ledger_path)


@pytest.fixture
def verifier(settings, key):
    return UserTokenVerifier(settings, key_for=lambda token: key.public_key())


@pytest.fixture
def make_token(settings, key):
    def make(roles=("Chat.User",), scp="Chat.Access", **over):
        now = int(time.time())
        claims = {"iss": settings.issuer, "aud": CHAT_APP, "sub": "s", "oid": OWNER, "tid": TENANT, "iat": now,
                  "exp": now + 3600, "name": "Owner", "roles": list(roles), "scp": scp, "jti": str(uuid.uuid4())}
        claims.update(over)
        return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")
    return make
