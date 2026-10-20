from __future__ import annotations

import json
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from chess_api import trino_client
from chess_api.settings import Settings
from chess_api.telemetry import JsonFormatter
from chess_api.trino_client import TrinoRunner, TrinoUnavailable


class FakeCursor:
    description = [("month",), ("games",)]

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if self.conn.error:
            raise self.conn.error

    def fetchall(self):
        return [["2015-12", 120]]


class FakeConnection:
    def __init__(self, **kwargs):
        self.kwargs, self.executed, self.error, self.closed = kwargs, [], None, False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


@pytest.fixture
def connections(monkeypatch):
    made: list[FakeConnection] = []

    def connect(**kwargs):
        made.append(FakeConnection(**kwargs))
        return made[-1]

    monkeypatch.setattr(trino_client.trino.dbapi, "connect", connect)
    return made


def test_signs_in_as_the_service_account_with_a_fresh_token(tmp_path, connections):
    token = tmp_path / "token"
    token.write_text("tok-1\n")
    runner = TrinoRunner(Settings(trino_token_file=token, trino_ca_file=tmp_path / "ca.crt"))
    assert runner.query("SELECT 1 WHERE x = ?", ["a"]) == [{"month": "2015-12", "games": 120}]
    kw = connections[0].kwargs
    # The user must match the token's principal, or Trino sees impersonation and denies it.
    assert (kw["host"], kw["port"], kw["user"]) == ("trino.trino.svc.cluster.local", 8443, "chess-api")
    assert (kw["catalog"], kw["schema"], kw["http_scheme"]) == ("lake", "gold", "https")
    assert kw["verify"] == str(tmp_path / "ca.crt")
    assert kw["auth"].token == "tok-1"
    assert connections[0].executed == [("SELECT 1 WHERE x = ?", ["a"])]
    assert connections[0].closed

    token.write_text("tok-2\n")  # the kubelet rotated the projected token
    runner.query("SELECT 1")
    assert connections[1].kwargs["auth"].token == "tok-2"
    assert connections[1].executed == [("SELECT 1", None)]


def test_errors_become_trino_unavailable(tmp_path, connections, monkeypatch):
    token = tmp_path / "token"
    token.write_text("t")
    runner = TrinoRunner(Settings(trino_token_file=token))
    orig = trino_client.trino.dbapi.connect

    def failing(**kw):
        conn = orig(**kw)
        conn.error = RuntimeError("Access Denied: Cannot select from table lake.curated.games")
        return conn

    monkeypatch.setattr(trino_client.trino.dbapi, "connect", failing)
    with pytest.raises(TrinoUnavailable):
        runner.query("SELECT 1")
    assert connections[-1].closed


def test_missing_token_is_unavailable(tmp_path):
    with pytest.raises(TrinoUnavailable):
        TrinoRunner(Settings(trino_token_file=tmp_path / "missing")).query("SELECT 1")


def test_json_logs_carry_extra_fields_and_trace_id():
    tracer = TracerProvider().get_tracer("t")
    record = logging.makeLogRecord({"name": "chess_api", "levelno": 20, "levelname": "INFO", "msg": "request",
                                    "status": 200, "path": "/api/v1/months"})
    with trace.use_span(tracer.start_span("s"), end_on_exit=True):
        out = json.loads(JsonFormatter().format(record))
    assert out["msg"] == "request" and out["level"] == "info"
    assert out["status"] == 200 and out["path"] == "/api/v1/months"
    assert len(out["trace_id"]) == 32
