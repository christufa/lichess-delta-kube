"""chess-mcp: the lakehouse MCP server (#68)."""
from __future__ import annotations

import json
import logging
import sys

_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName", "color_message"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line; `extra=` fields become keys (audit records carry user, SQL, rows)."""

    def format(self, record: logging.LogRecord) -> str:
        out = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        out.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True


def main() -> None:
    import uvicorn

    from .server import create_app
    from .settings import Settings

    settings = Settings()
    setup_logging(settings.log_level)
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.http_port, log_config=None,  # noqa: S104
                proxy_headers=True, forwarded_allow_ips="*")
