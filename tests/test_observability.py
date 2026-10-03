"""Logging is useful before Spark startup and during long operations."""
import json
import logging
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lichess_pipeline import common, observability


@pytest.fixture(autouse=True)
def reset_logging():
    yield
    for handler in list(observability.LOGGER.handlers):
        observability.LOGGER.removeHandler(handler)
        handler.close()
    observability.CONTEXT.clear()
    observability.LOGGER.propagate = True


def test_failure_logs_context_and_redacts_secrets(capsys):
    observability.configure_logging("upload", "123")
    with pytest.raises(RuntimeError):
        with observability.operation("hf.upload"):
            raise RuntimeError("hf_secret123 Bearer abc123 https://storage.example/blob?sig=private&key=hidden")
    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines()]
    assert [r["event"] for r in records] == ["hf.upload.started", "hf.upload.failed"]
    assert records[-1]["stage"] == "upload"
    assert records[-1]["run_id"] == "123"
    assert "RuntimeError" in records[-1]["traceback"]
    for secret in ("hf_secret123", "abc123", "sig=private", "key=hidden"):
        assert secret not in output
    assert "hf.upload.completed" not in output


def test_operation_reports_liveness_and_stops_heartbeat(monkeypatch):
    records = []
    seen = threading.Event()
    def capture(name, **fields):
        records.append(name)
        if name.endswith(".running"):
            seen.set()
    monkeypatch.setattr(observability, "event", capture)
    with observability.operation("delta.write", heartbeat_seconds=0.01):
        assert seen.wait(timeout=1)
    assert records[0] == "delta.write.started"
    assert records[-1] == "delta.write.completed"
    assert "delta.write.running" in records


def test_task_logs_failure_before_spark_initializes(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["download", "--catalog", "brikt", "--schema", "dev",
                        "--volume", "staging", "--run-id", "123", "--table", "games",
                        "--repo", "owner/data"])
    monkeypatch.setitem(sys.modules, "pyspark", None)
    monkeypatch.setitem(sys.modules, "pyspark.sql", None)
    with pytest.raises(ModuleNotFoundError):
        common.run_task(lambda *args: pytest.fail("Handler must not run"), "download")
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0]["event"] == "task.started"
    assert records[-1]["event"] == "task.failed"
    assert records[-1]["run_id"] == "123"
