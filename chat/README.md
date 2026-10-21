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
minute, `413` conversation too long, `503` not configured (Claude sign-in not set up yet).

## How it works

- **Model:** `anthropic:claude-opus-5-5` by default (`CHESS_CHAT_MODEL`, any Pydantic AI
  `provider:model` with a price in `pricing.py`).
  - Adaptive thinking with summarized reasoning, so the panel can show how it got there, at effort `medium`.
  - Cached instructions and tool definitions.
  - Anthropic's server-side fallback (`fallbacks: "default"`): if a safety classifier declines, the API retries on a suitable model.
- **Claude sign-in, no API key:** Anthropic [Workload Identity Federation](https://platform.claude.com/docs/en/manage-claude/workload-identity-federation).
  - The pod gets an Entra token for the "Claude API audience" app (`infra/gitops/chat.tf`) as its managed identity.
  - Anthropic checks it against a federation rule (this tenant's issuer, that audience, the identity's object ID) and returns a short-lived token for an Anthropic service account. The SDK refreshes it.
  - Only the chat identity is assigned to the audience app, so no other identity in the tenant can get a token for it.
- **Tools:** the MCP server's (`list_tables`, `describe_table`, `run_sql`, `get_game`) and nothing else.
  - The backend signs in to it as its own managed identity (workload identity, client credentials, no secret), which holds the MCP app's role `Lakehouse.Query`.
  - The MCP server's read-only guard and Trino's rules apply to everything the model runs.
- **Spend cap:** every model request is priced from the provider's reported usage as it ends, even when the stream is cut off. Spend is recorded per user and day in SQLite on a persistent disk.
  - New questions are refused once today's total reaches **$5** (`dailyCapUsd`) or a user's reaches their cap (`userDailyCapUsd`).
  - One replica owns the disk, so the cap is one number and survives restarts.
- **Limits per answer:** 12 model requests, 20 tool calls.
- **Logs:** one JSON line per model request (user, model, tokens, cost). The MCP server logs the SQL.

## Setup (owner)

1. Apply `infra/compute` (the chat's managed identity), then `infra/gitops`:
   - the "chess Chat" app
   - the MCP role for the identity
   - the "Claude API audience" app and its role for the identity
   - the facts
   - the `/api/v1/chat` route
2. Connect the chat to Claude in the [Claude Console](https://platform.claude.com/settings/workload-identity-federation). You need the admin or owner role in your Anthropic organization.
   1. Print what the wizard asks for:
      ```powershell
      terraform -chdir=infra/gitops output claude_federation
      ```
   2. Under **Settings → Workload identity → Connect workload**, choose **Microsoft Entra** and set **Token issuer** to **v2.0 (login.microsoftonline.com)**.
      - Issuer URL: `issuer_url`
      - Rule match: audience = `audience`, claim `oid` = `oid`, claim `tid` = `tid`
      - Scope `workspace:developer`. Keep the default token lifetime (600 s).
      - Enable the rule in one workspace (a dedicated `chess-chat` workspace can have its own spend limit), not all.
   3. Edit the issuer (**Issuers** tab) and set its max JWT lifetime to **25 hours**. The wizard sets about 2 hours, but managed identity tokens from Entra last 24 hours plus 5 minutes, and anything longer than the limit is refused.
   4. Note the service account ID (`svac_...`), the rule ID (`fdrl_...`), your organization ID (Settings → Organization) and the workspace ID (`wrkspc_...`, Settings → Workspaces). The workspace ID is needed even when the rule covers only one workspace; without it the exchange fails with `workspace_id_required`.
3. Put those IDs in `infra/gitops/terraform.tfvars` (gitignored; see `terraform.tfvars.example`) and apply `infra/gitops` again:
   ```hcl
   anthropic_federation = {
     organization_id    = "<org uuid>"
     service_account_id = "svac_..."
     federation_rule_id = "fdrl_..."
     workspace_id       = "wrkspc_..."
   }
   ```
   Argo CD restarts the chat with the new values.
4. Check it: `/api/v1/chat/status` says `available: true`. The caller only ever gets `401 Authentication failed`; the Console's **Authentication history** tab lists every exchange with the reason a rejected one failed (`status.reason`).
5. Give someone access: add their sign-in name to `mcp_extra_users` and apply `infra/gitops`.

To run it locally, set `ANTHROPIC_API_KEY` in your shell instead. Federation is used only when `CHESS_CHAT_ANTHROPIC_FEDERATION_RULE_ID` is set.

## Run and test

```powershell
cd chat
uv sync
uv run ruff check .
uv run pytest -q   # Pydantic AI's TestModel stands in for the provider; Entra tokens signed with a local key
```
