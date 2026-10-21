import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from chess_mcp.auth import EntraTokenVerifier
from chess_mcp.settings import Settings

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
OWNER = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(scope="session")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def settings():
    return Settings(tenant_id=TENANT, client_id=CLIENT, public_url="https://chess-mcp.example.test",
                    queries_per_minute=5)


@pytest.fixture
def verifier(settings, key):
    return EntraTokenVerifier(settings, key_for=lambda token: key.public_key())


@pytest.fixture
def make_token(settings, key):
    def make(roles=("Lakehouse.Query",), scp="MCP.Access", **over):
        now = int(time.time())
        claims = {
            "iss": settings.issuer, "aud": CLIENT, "sub": "subject", "oid": OWNER, "tid": TENANT,
            "azp": "client-app", "iat": now, "nbf": now, "exp": now + 3600, "preferred_username": "owner@example.test",
            "roles": list(roles), "scp": scp, "jti": str(uuid.uuid4()),
        }
        claims.update(over)
        return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")
    return make


class FakeDb:
    """Answers the catalog queries and records everything else."""

    def __init__(self):
        self.queries = []

    def query(self, sql, params=(), max_rows=None):
        self.queries.append((sql, tuple(params)))
        if "information_schema.tables" in sql:
            return ["table_schema", "table_name", "comment"], [["curated", "games", "One typed row per game"],
                                                                ["gold", "openings", "Openings per month"]]
        if sql.startswith("SHOW COLUMNS"):
            return ["Column", "Type", "Extra", "Comment"], [["eco", "varchar", "", ""], ["games", "bigint", "", ""]]
        if "curated.games WHERE game_id" in sql:
            if params[0] == "AbCd1234":
                return ["game_id", "white", "result"], [["AbCd1234", "alice", "white"]]
            return ["game_id"], []
        n = 3 if max_rows is None else min(3, max_rows)
        return ["eco", "games"], [["A00", i] for i in range(n)]


@pytest.fixture
def db():
    return FakeDb()
