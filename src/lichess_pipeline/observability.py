"""JSON task logs and heartbeat events for long-running driver operations."""
import json
import logging
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

LOGGER = logging.getLogger("lichess_pipeline")
CONTEXT = {}


def redact(text):
    text = re.sub(r"hf_[A-Za-z0-9]+", "[REDACTED_TOKEN]", text)
    text = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)
    return re.sub(r"(https?://[^\s?\"']+)\?[^\s\"']+", r"\1?[REDACTED_QUERY]", text)


def _redact_fields(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redact_fields(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_fields(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {"timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname, **CONTEXT,
                "event": record.getMessage(), **getattr(record, "fields", {})}
        if record.exc_info:
            data["traceback"] = self.formatException(record.exc_info)
        return json.dumps(_redact_fields(data), default=str)


def configure_logging(stage, run_id):
    CONTEXT.clear()
    CONTEXT.update(stage=stage, run_id=run_id)
    for handler in list(LOGGER.handlers):
        LOGGER.removeHandler(handler)
        handler.close()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def event(name, **fields):
    LOGGER.info(name, extra={"fields": fields})


@contextmanager
def operation(name, heartbeat_seconds=60, **fields):
    """Report liveness, not fabricated Spark/HF percentage progress."""
    started = time.monotonic()
    stopped = threading.Event()
    event(f"{name}.started", **fields)
    def heartbeat():
        while not stopped.wait(heartbeat_seconds):
            event(f"{name}.running", elapsed_seconds=round(time.monotonic() - started, 2), **fields)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        yield
    except BaseException:
        LOGGER.exception(f"{name}.failed", extra={"fields": {
            **fields, "elapsed_seconds": round(time.monotonic() - started, 2)}})
        raise
    else:
        stopped.set()
        thread.join(timeout=1)
        event(f"{name}.completed", elapsed_seconds=round(time.monotonic() - started, 2), **fields)
    finally:
        stopped.set()
        thread.join(timeout=1)
