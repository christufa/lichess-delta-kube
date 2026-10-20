"""Trino access. Everything goes through `QueryRunner`, so tests swap in a fake."""
from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any, Protocol

import trino
from opentelemetry import trace
from trino.auth import JWTAuthentication

from .settings import Settings

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

Row = dict[str, Any]


class TrinoUnavailable(Exception):
    """Trino couldn't be reached or the query failed; the API answers 503."""


class QueryRunner(Protocol):
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[Row]: ...


class TrinoRunner:
    """Runs one query per connection over HTTPS, signed in with the projected token.

    The token file is read on every query: the kubelet rotates it well before it expires.
    """

    def __init__(self, settings: Settings):
        self.s = settings

    def _connect(self):
        token = self.s.trino_token_file.read_text().strip()
        return trino.dbapi.connect(
            host=self.s.trino_host,
            port=self.s.trino_port,
            user=self.s.trino_user,
            catalog=self.s.trino_catalog,
            schema=self.s.trino_schema,
            http_scheme="https",
            auth=JWTAuthentication(token),
            verify=str(self.s.trino_ca_file),
            request_timeout=self.s.trino_timeout_seconds,
            source="chess-api",
        )

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[Row]:
        with tracer.start_as_current_span("trino.query") as span:
            span.set_attribute("db.system.name", "trino")
            span.set_attribute("db.query.text", sql)
            started = time.perf_counter()
            try:
                conn = self._connect()
                try:
                    cur = conn.cursor()
                    cur.execute(sql, list(params)) if params else cur.execute(sql)
                    rows = cur.fetchall()
                    names = [d[0] for d in cur.description or []]
                finally:
                    conn.close()
            except Exception as e:  # network, TLS, auth, access denied, query errors
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR)
                log.error("trino query failed", extra={"error": f"{type(e).__name__}: {e}"})
                raise TrinoUnavailable(str(e)) from e
            ms = round((time.perf_counter() - started) * 1000, 1)
            span.set_attribute("db.response.returned_rows", len(rows))
            log.info("trino query", extra={"rows": len(rows), "duration_ms": ms})
            return [dict(zip(names, r, strict=True)) for r in rows]
