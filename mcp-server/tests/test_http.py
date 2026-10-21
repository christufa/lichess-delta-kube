"""End to end over HTTP with the real auth middleware, the real MCP transport and a fake Trino."""
import json

import pytest
from starlette.testclient import TestClient

from chess_mcp.server import create_app

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "MCP-Protocol-Version": "2025-06-18"}


@pytest.fixture
def client(settings, db, verifier):
    with TestClient(create_app(settings, db, verifier), base_url="http://testserver") as c:
        yield c


def rpc(client, method, params=None, token=None, id_=1):
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", headers=headers,
                       content=json.dumps({"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}))


def call(client, token, name, arguments):
    r = rpc(client, "tools/call", {"name": name, "arguments": arguments}, token)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    if not result.get("isError"):
        result["data"] = json.loads(result["content"][0]["text"])
    return result


def test_unauthenticated_gets_401_with_resource_metadata(client):
    r = rpc(client, "tools/list")
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    assert ".well-known/oauth-protected-resource" in r.headers["www-authenticate"]


def test_protected_resource_metadata(client, settings):
    r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    meta = r.json()
    assert meta["resource"].rstrip("/") == settings.public_url
    assert meta["authorization_servers"] == [settings.issuer]
    assert meta["scopes_supported"] == [settings.scope]


def test_token_without_role_gets_403(client, make_token):
    r = rpc(client, "tools/list", token=make_token(roles=()))
    assert r.status_code == 403
    assert "insufficient_scope" in r.headers.get("www-authenticate", "") + r.text


def test_tools_listed_and_read_only(client, make_token):
    r = rpc(client, "tools/list", token=make_token())
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == {"list_tables", "describe_table", "run_sql", "get_game"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools.values())


def test_run_sql_limits_and_qualifies(client, make_token, db):
    result = call(client, make_token(), "run_sql", {"sql": "SELECT eco, games FROM gold.openings"})
    out = result["data"]
    assert out["columns"] == ["eco", "games"] and out["row_count"] == 3 and not out["truncated"]
    assert db.queries[-1][0].startswith("SELECT eco, games FROM lake.gold.openings")


def test_write_sql_is_rejected_before_trino(client, make_token, db):
    result = call(client, make_token(), "run_sql", {"sql": "DELETE FROM gold.openings"})
    assert result["isError"] and "SELECT" in result["content"][0]["text"]
    assert not db.queries


def test_other_schema_is_rejected(client, make_token):
    result = call(client, make_token(), "run_sql", {"sql": "SELECT * FROM raw.games"})
    assert result["isError"]


def test_get_game(client, make_token):
    found = call(client, make_token(), "get_game", {"game_id": "AbCd1234"})["data"]
    assert found["found"] and found["url"] == "https://lichess.org/AbCd1234"
    assert call(client, make_token(), "get_game", {"game_id": "Zzzz9999"})["data"]["found"] is False
    assert call(client, make_token(), "get_game", {"game_id": "x'; DROP"})["isError"]


def test_describe_table_only_known_tables(client, make_token):
    ok = call(client, make_token(), "describe_table", {"table": "gold.openings"})["data"]
    assert ok["columns"][0]["name"] == "eco"
    assert call(client, make_token(), "describe_table", {"table": "raw.games"})["isError"]
    assert call(client, make_token(), "describe_table", {"table": "gold.nope"})["isError"]


def test_rate_limit_per_user(client, make_token):
    token = make_token()
    results = [call(client, token, "run_sql", {"sql": "SELECT 1 AS x FROM gold.openings"}) for _ in range(6)]
    assert not any(r.get("isError") for r in results[:5]) and results[5]["isError"]
    assert "rate limit" in results[5]["content"][0]["text"]
    other = make_token(oid="44444444-4444-4444-4444-444444444444")
    assert not call(client, other, "run_sql", {"sql": "SELECT 1 AS x FROM gold.openings"}).get("isError")


def test_resources(client, make_token):
    r = rpc(client, "resources/list", token=make_token())
    assert {x["uri"] for x in r.json()["result"]["resources"]} == {"lake://docs/tables", "lake://docs/examples"}


def test_healthz_needs_no_token(client):
    assert client.get("/healthz").text == "ok\n"


def test_foreign_host_rejected(client, make_token):
    r = client.post("/mcp", headers={**HEADERS, "Host": "evil.example", "Authorization": f"Bearer {make_token()}"},
                    content="{}")
    assert r.status_code in (400, 403, 421)
