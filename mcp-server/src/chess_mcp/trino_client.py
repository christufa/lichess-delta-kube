"""Trino access as chess-mcp (read-only: lake.curated, lake.gold). Tests swap in a fake runner."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import trino
from trino.auth import JWTAuthentication

from .settings import Settings

Row = dict[str, Any]


class QueryFailed(Exception):
    """Trino rejected or couldn't run the query (the message is safe to show)."""


class QueryRunner(Protocol):
    def query(
        self, sql: str, params: Sequence[Any] = (), max_rows: int | None = None
    ) -> tuple[list[str], list[list[Any]]]: ...


class TrinoRunner:
    """One connection per query over HTTPS, signed in with the pod's projected token (re-read
    each time: the kubelet rotates it). Every query gets Trino's own execution time limit."""

    def __init__(self, settings: Settings):
        self.s = settings

    def query(self, sql: str, params: Sequence[Any] = (), max_rows: int | None = None):
        conn = trino.dbapi.connect(
            host=self.s.trino_host,
            port=self.s.trino_port,
            user=self.s.trino_user,
            catalog=self.s.trino_catalog,
            http_scheme="https",
            auth=JWTAuthentication(self.s.trino_token_file.read_text().strip()),
            verify=str(self.s.trino_ca_file),
            request_timeout=self.s.query_timeout_seconds + 10,
            session_properties={"query_max_execution_time": f"{self.s.query_timeout_seconds}s"},
            source="chess-mcp",
        )
        try:
            cur = conn.cursor()
            cur.execute(sql, list(params)) if params else cur.execute(sql)
            rows = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
            names = [d[0] for d in cur.description or []]
            return names, [list(r) for r in rows]
        except trino.exceptions.TrinoUserError as e:
            raise QueryFailed(f"{e.error_name}: {e.message}") from e
        except trino.exceptions.TrinoQueryError as e:
            raise QueryFailed(f"{e.error_name}: {e.message}") from e
        except Exception as e:  # noqa: BLE001  network, TLS, auth
            raise QueryFailed(f"the data store is unavailable ({type(e).__name__})") from e
        finally:
            conn.close()
