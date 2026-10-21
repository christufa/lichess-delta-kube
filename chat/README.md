# chess-chat: chat on the Lichess lakehouse

The backend of the dashboard's chat panel (#69): a model-agnostic agent ([Pydantic AI](https://ai.pydantic.dev))
whose **only** way to the data is the lakehouse MCP server ([mcp-server/](../mcp-server/README.md)).
It streams answers to the browser in the [Vercel AI SDK](https://ai-sdk.dev) UI message protocol.

> **Access:** `POST /api/v1/chat` needs a Microsoft Entra ID token for the "chess Chat" app with
> app role `Chat.User`. Only the owner (and anyone in `mcp_extra_users`) is assigned. Everyone can
> see the panel; others get `401` (not signed in) or `403` (not assigned).

## Endpoints

| Endpoint | What |
|---|---|
| `POST /api/v1/chat` | one chat turn: the conversation (Vercel AI SDK `messages`) in, a UI message stream out (text, reasoning summaries, tool calls) |
| `GET /api/v1/chat/status` | for a signed-in user: whether the chat is available, the model, spent and left today |
| `GET /healthz` | liveness; not routed publicly |

Errors: `401` sign in, `403` not assigned, `429` daily budget used up or more than 6 questions a
minute, `413` conversation too long, `503` not configured (no model API key yet).

## How it works

- **Model:** `anthropic:claude-opus-5-5` by default (`CHESS_CHAT_MODEL`, any Pydantic AI
  `provider:model` with a price in `pricing.py`).
  - Adaptive thinking with summarized reasoning, so the panel can show how it got there, at effort `medium`.
  - Cached instructions and tool definitions.
  - Anthropic's server-side fallback (`fallbacks: "default"`): if a safety classifier declines, the API retries on a suitable model.
- **Tools:** the MCP server's (`list_tables`, `describe_table`, `run_sql`, `get_game`) and nothing else.
  - The backend signs in to it as its own managed identity (workload identity, client credentials, no secret), which holds the MCP app's role `Lakehouse.Query`.
  - The MCP server's read-only guard and Trino's rules apply to everything the model runs.
- **Spend cap:** every model request is priced from the provider's reported usage as it ends, even when the stream is cut off. Spend is recorded per user and day in SQLite on a persistent disk.
  - New questions are refused once today's total reaches **$5** (`dailyCapUsd`) or a user's reaches their cap (`userDailyCapUsd`).
  - One replica owns the disk, so the cap is one number and survives restarts.
- **Limits per answer:** 12 model requests, 20 tool calls.
- **Logs:** one JSON line per model request (user, model, tokens, cost). The MCP server logs the SQL.

## Setup (owner)

1. Apply `infra/compute` (the chat's managed identity), then `infra/gitops` (the "chess Chat" app, the MCP role for the identity, the facts, the `/api/v1/chat` route).
2. Put the model API key in Key Vault, from a terminal you're signed in on:
   ```powershell
   az keyvault secret set --vault-name "$(terraform -chdir=infra/compute output -raw key_vault_name)" --name anthropic-api-key --value "<your Anthropic API key>"
   ```
   External Secrets copies it into the cluster within the hour. To pick it up immediately, restart: `kubectl rollout restart deploy/chess-chat -n chess-chat`.
3. Give someone access: add their sign-in name to `mcp_extra_users` and apply `infra/gitops`.

## Run and test

```powershell
cd chat
uv sync
uv run ruff check .
uv run pytest -q   # Pydantic AI's TestModel stands in for the provider; Entra tokens signed with a local key
```
