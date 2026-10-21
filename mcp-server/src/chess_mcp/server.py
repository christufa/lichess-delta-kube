"""The MCP server: tools and resources over lake.curated and lake.gold, behind Entra sign-in.

Streamable HTTP at <public_url>/mcp, stateless (any replica can answer any request). Clients
discover how to sign in from the protected resource metadata at
<public_url>/.well-known/oauth-protected-resource; tokens are checked by auth.EntraTokenVerifier.
"""
from __future__ import annotations

import datetime as dt
import decimal
import logging
import re
import threading
import time
from collections import deque
from urllib.parse import urlparse

import anyio
from cachetools import TTLCache
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import catalog, sql_guard
from .auth import EntraTokenVerifier, user_of
from .settings import Settings
from .trino_client import QueryFailed, QueryRunner, TrinoRunner

log = logging.getLogger("chess_mcp")
audit = logging.getLogger("chess_mcp.audit")

GAME_ID = re.compile(r"^[A-Za-z0-9]{8}$")
IN_CLUSTER = "chess-mcp.chess-mcp.svc.cluster.local"  # the chat backend's address for this server
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = """\
Read-only SQL access to the Lichess lakehouse: rated standard games from the Lichess open
database, as Iceberg tables in Trino. Start with the `lake://docs/tables` resource or the
`list_tables` / `describe_table` tools, prefer the small `gold` tables, filter on `month`, and
write tables as schema.table (curated.games, gold.openings, ...). `run_sql` takes one SELECT.
"""


def jsonable(v):
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


class RateLimiter:
    """At most `per_minute` calls per user in any 60 seconds."""

    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute, self.clock = per_minute, clock
        self._calls: TTLCache[str, deque] = TTLCache(maxsize=10_000, ttl=120, timer=clock)
        self._lock = threading.Lock()

    def check(self, user: str) -> None:
        if self.per_minute <= 0:
            return
        now = self.clock()
        with self._lock:
            calls = self._calls.get(user) or deque()
            while calls and now - calls[0] >= 60:
                calls.popleft()
            if len(calls) >= self.per_minute:
                raise ToolError(f"rate limit: at most {self.per_minute} queries a minute; try again in a moment")
            calls.append(now)
            self._calls[user] = calls


def create_server(settings: Settings, db: QueryRunner, verifier) -> MCPServer:
    limiter = RateLimiter(settings.queries_per_minute)
    server = MCPServer(
        name="Lichess lakehouse",
        instructions=INSTRUCTIONS,
        website_url="https://chess.generops.dev",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=settings.issuer,
            resource_server_url=settings.public_url,
            required_scopes=[settings.scope],
            validate_token_resource=False,  # Entra's `aud` is the app; EntraTokenVerifier checks it
        ),
    )

    async def run(tool: str, sql: str, params=(), max_rows: int | None = None):
        """Rate-limit, run in a worker thread, and audit-log every query."""
        who = user_of(get_access_token())
        limiter.check(who["oid"])
        started = time.perf_counter()
        status, rows, error = "ok", 0, None
        try:
            names, data = await anyio.to_thread.run_sync(lambda: db.query(sql, params, max_rows))
            rows = len(data)
            return names, [[jsonable(v) for v in r] for r in data]
        except QueryFailed as e:
            status, error = "failed", str(e)
            raise ToolError(str(e)) from e
        finally:
            audit.info("query", extra={**who, "tool": tool, "sql": sql, "rows": rows, "status": status, "error": error,
                                       "duration_ms": round((time.perf_counter() - started) * 1000, 1)})

    @server.tool(annotations=READ_ONLY)
    async def list_tables() -> dict:
        """The tables you can query (curated.* and gold.*), each with a one-line description."""
        who = user_of(get_access_token())
        limiter.check(who["oid"])
        return {"tables": await anyio.to_thread.run_sync(lambda: catalog.list_tables(db))}

    @server.tool(annotations=READ_ONLY)
    async def describe_table(table: str) -> dict:
        """Columns (name, type, description) and 5 sample rows of one table, e.g. 'gold.openings'."""
        who = user_of(get_access_token())
        limiter.check(who["oid"])
        try:
            result = await anyio.to_thread.run_sync(lambda: catalog.describe_table(db, table))
        except ValueError as e:
            raise ToolError(str(e)) from e
        result["sample"]["rows"] = [[jsonable(v) for v in r] for r in result["sample"]["rows"]]
        return result

    @server.tool(annotations=READ_ONLY)
    async def run_sql(sql: str) -> dict:
        """Run one read-only SELECT (Trino SQL) over curated.* and gold.* tables. Write tables as
        schema.table and filter on `month` (YYYY-MM) where you can. At most 1000 rows come back
        (`truncated` says if there were more); queries stop after 30 seconds."""
        try:
            checked = sql_guard.check(sql, settings.max_rows)
        except sql_guard.SqlRejected as e:
            who = user_of(get_access_token())
            audit.info("query", extra={**who, "tool": "run_sql", "sql": sql, "rows": 0, "status": "rejected",
                                       "error": str(e), "duration_ms": 0})
            raise ToolError(str(e)) from e
        names, rows = await run("run_sql", checked.sql, max_rows=settings.max_rows + 1)
        truncated = len(rows) > settings.max_rows
        return {"columns": names, "rows": rows[: settings.max_rows], "row_count": min(len(rows), settings.max_rows),
                "truncated": truncated, "tables": checked.tables, "executed_sql": checked.sql}

    @server.tool(annotations=READ_ONLY)
    async def get_game(game_id: str) -> dict:
        """One game by its Lichess id (the 8 characters in lichess.org/<id>): players, ratings,
        result, opening, time control, when it was played."""
        if not GAME_ID.match(game_id or ""):
            raise ToolError("game_id is the 8 letters and digits in lichess.org/<game_id>")
        names, rows = await run("get_game", "SELECT * FROM lake.curated.games WHERE game_id = ? AND NOT is_duplicate",
                                (game_id,), max_rows=1)
        if not rows:
            return {"found": False, "game_id": game_id}
        return {"found": True, "url": f"https://lichess.org/{game_id}", "game": dict(zip(names, rows[0], strict=True))}

    @server.resource("lake://docs/tables", name="tables", title="Tables and columns", mime_type="text/markdown")
    def tables_doc() -> str:
        """What every table holds and how to query it well."""
        return catalog.TABLE_DOCS

    @server.resource("lake://docs/examples", name="examples", title="Example questions", mime_type="text/markdown")
    def examples_doc() -> str:
        """Example questions with the SQL that answers them."""
        return catalog.EXAMPLES

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok\n")

    return server


def create_app(settings: Settings | None = None, db: QueryRunner | None = None, verifier=None):
    settings = settings or Settings()
    server = create_server(settings, db or TrinoRunner(settings), verifier or EntraTokenVerifier(settings))
    public_host = urlparse(settings.public_url).netloc
    return server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        host="0.0.0.0",  # noqa: S104  (behind cloudflared and a network policy)
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public_host, IN_CLUSTER, f"{IN_CLUSTER}:*", "localhost:*", "127.0.0.1:*", "testserver"],
            allowed_origins=[f"https://{public_host}", "https://claude.ai", "https://chess.generops.dev",
                             "http://localhost:*", "http://127.0.0.1:*"],
        ),
    )
