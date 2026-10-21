"""The HTTP endpoint end to end: gate, budget, rate limit and a real Vercel AI stream, with
Pydantic AI's TestModel in place of the provider and a local tool in place of the MCP server."""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from chess_chat.agent import build_agent
from chess_chat.app import create_app


def fake_lake() -> FunctionToolset:
    tools = FunctionToolset()

    @tools.tool_plain
    def run_sql(sql: str) -> dict:
        """Run one SELECT."""
        return {"columns": ["games"], "rows": [[89616462]]}

    return tools


@pytest.fixture
def agent(settings, ledger):
    return build_agent(settings, ledger, model=TestModel(), toolsets=[fake_lake()])


@pytest.fixture
def client(settings, ledger, verifier, agent):
    return TestClient(create_app(settings, ledger=ledger, agent=agent, verifier=verifier))


def ask(client, token, text="How many games in September 2026?"):
    body = {"trigger": "submit-message", "id": "chat-1",
            "messages": [{"id": "m1", "role": "user", "parts": [{"type": "text", "text": text}]}]}
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/v1/chat", json=body, headers=headers)


def test_no_token_401(client):
    r = ask(client, None)
    assert r.status_code == 401 and r.json()["code"] == "sign_in"


def test_not_assigned_403(client, make_token):
    r = ask(client, make_token(roles=()))
    assert r.status_code == 403 and r.json()["code"] == "not_allowed"


def test_streams_an_answer_and_charges_it(client, make_token, ledger):
    r = ask(client, make_token())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
    types = {e["type"] for e in events}
    assert {"start", "finish"} <= types and any(t.startswith("tool-") for t in types), types
    assert ledger.spent() > 0  # every model request was charged


def test_status(client, make_token, ledger):
    ledger.add("33333333-3333-3333-3333-333333333333", 1.25)
    s = client.get("/api/v1/chat/status", headers={"Authorization": f"Bearer {make_token()}"}).json()
    assert s["available"] and s["spent_today_usd"] == 1.25 and s["left_today_usd"] == 3.75


def test_daily_budget_429(client, make_token, ledger):
    ledger.add("someone-else", 5.0)  # the shared cap is spent
    r = ask(client, make_token())
    assert r.status_code == 429 and r.json()["code"] == "daily_budget"


def test_rate_limit_429(client, make_token):
    token = make_token()
    codes = [ask(client, token).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_unavailable_without_a_model_key(settings, ledger, verifier, make_token, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = TestClient(create_app(settings, ledger=ledger, verifier=verifier))  # builds the real agent: no key
    r = ask(c, make_token())
    assert r.status_code == 503 and r.json()["code"] == "unavailable"
    status = c.get("/api/v1/chat/status", headers={"Authorization": f"Bearer {make_token()}"}).json()
    assert status["available"] is False


def test_too_many_messages_413(client, make_token, settings):
    messages = [{"id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant",
                 "parts": [{"type": "text", "text": "hi"}]} for i in range(settings.max_messages + 1)]
    r = client.post("/api/v1/chat", json={"trigger": "submit-message", "id": "chat-1", "messages": messages},
                    headers={"Authorization": f"Bearer {make_token()}"})
    assert r.status_code == 413 and r.json()["code"] == "too_large"


def test_healthz(client):
    assert client.get("/healthz").text == "ok\n"
