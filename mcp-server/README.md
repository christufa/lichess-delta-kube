# chess-mcp: the lakehouse MCP server

An [MCP](https://modelcontextprotocol.io) server over the Lichess lakehouse (#68), for MCP clients
such as Claude, and for the dashboard's chat (#69). Streamable HTTP at
**`https://chess-mcp.generops.dev/mcp`**.

> **Access:** every request needs a Microsoft Entra ID token for the "chess MCP" app with app role
> `Lakehouse.Query`. Only the owner (and anyone listed in `mcp_extra_users`) is assigned. Anyone
> else gets `401` without a token, or `403` with a token from an unassigned account.

## Tools and resources

| Tool | What it does |
|---|---|
| `list_tables` | the tables you can query (`curated.*`, `gold.*`) with a one-line description each |
| `describe_table(table)` | columns (name, type, description) and 5 sample rows, e.g. `gold.openings` |
| `run_sql(sql)` | one read-only `SELECT` (Trino SQL) over `curated.*` and `gold.*`; at most 1000 rows (`truncated` says if there were more), 30 s timeout |
| `get_game(game_id)` | one game by its Lichess id (`lichess.org/<id>`) |

All tools are marked read-only. Resources: `lake://docs/tables` (what every table holds and how to
query it well) and `lake://docs/examples` (example questions with SQL).

Not yet: `explore_position` (FEN → moves) needs the opening explorer table (#65).

**Guardrails:**
- `run_sql` parses the SQL with [sqlglot](https://github.com/tobymao/sqlglot) and rejects anything but a single query. That covers writes, DDL, `CALL`, `SET`, other catalogs or schemas (`raw`, `ops`, `system`…), table functions and more than one statement.
- It adds a `LIMIT`.
- Trino would refuse the rest anyway: the server runs as the read-only user `chess-mcp`, whose rules allow `lake.curated` and `lake.gold` only, and the catalog itself is read-only.
- Each user is limited to 30 queries a minute.
- Every query is audit-logged as one JSON line: user, client, tool, SQL, rows, duration, status.

## Sign-in

The server is an OAuth 2.1 resource server ([MCP authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)):

- `GET /.well-known/oauth-protected-resource` names the authorization server (Entra:
  `https://login.microsoftonline.com/<tenant>/v2.0`) and the scope to request,
  `api://<client id>/MCP.Access`.
- Unauthenticated requests get `401` with `WWW-Authenticate: Bearer … resource_metadata=…`.
- Tokens are checked against Entra's signing keys, with issuer = the tenant, audience = this app. The required scope is granted only if the token carries both the delegated scope and the assigned app role.

Entra doesn't support dynamic client registration, so clients use the app's client ID (and, for
Claude, its secret). Get them after applying `infra/gitops`:

```powershell
terraform -chdir=infra/gitops output -raw mcp_client_id
terraform -chdir=infra/gitops output -raw mcp_scope
terraform -chdir=infra/gitops output -raw mcp_client_secret   # Claude only; rotates every 150 days
```

### Connect Claude (custom connector)

claude.ai → Settings → Connectors → **Add custom connector**:
1. URL: `https://chess-mcp.generops.dev/mcp`.
2. Under *Advanced settings*: the client ID and client secret from above.
3. Sign in with your Entra account when asked.

### MCP Inspector

`npx @modelcontextprotocol/inspector`, then:
1. Transport *Streamable HTTP*, URL `https://chess-mcp.generops.dev/mcp`.
2. Under *Authentication*: the client ID and the scope `api://<client id>/MCP.Access`.

### Quick check with the Azure CLI

The Azure CLI is pre-authorized for the scope, so the owner can get a token without a consent
prompt:

```powershell
$token = az account get-access-token --scope "$(terraform -chdir=infra/gitops output -raw mcp_scope)" --query accessToken -o tsv
curl.exe -s https://chess-mcp.generops.dev/mcp -H "Authorization: Bearer $token" -H "Content-Type: application/json" `
  -H "Accept: application/json, text/event-stream" -H "MCP-Protocol-Version: 2025-06-18" `
  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'
```

### Give someone else access

Add their Entra sign-in name to `mcp_extra_users` in `infra/gitops/terraform.tfvars` and apply
`infra/gitops`. That assigns them `Lakehouse.Query`, which also covers the chat. Remove them the
same way.

## Run and test

```powershell
cd mcp-server
uv sync
uv run ruff check .
uv run pytest -q   # Trino faked; Entra tokens signed with a local key, through the real auth middleware
```

Configuration is environment variables `CHESS_MCP_*` (`settings.py`). The Helm chart
(`deploy/charts/chess-mcp`) sets the tenant and client ID from the cluster facts.
