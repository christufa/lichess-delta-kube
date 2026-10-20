"""chess-api: the read-only data API over lake.gold for the public dashboard."""
from __future__ import annotations


def main() -> None:
    """Entry point of the container: JSON logs, traces, uvicorn on :8000."""
    import uvicorn

    from .app import create_app
    from .settings import Settings
    from .telemetry import setup_logging, setup_tracing

    settings = Settings()
    setup_logging(settings.log_level)
    setup_tracing()
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 (in a pod; the network policy decides who connects)
        port=8000,
        log_config=None,
        access_log=False,
        server_header=False,
        timeout_graceful_shutdown=20,
    )
