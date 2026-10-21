"""Configuration from the environment (CHESS_CHAT_*). Deployment-specific values (tenant, client
IDs) come from the cluster facts through the Helm chart; the model API key from Key Vault.

Avoid field names Kubernetes might inject for a Service named chess-chat (CHESS_CHAT_PORT,
CHESS_CHAT_SERVICE_HOST, ...); the chart also sets enableServiceLinks: false."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHESS_CHAT_")

    # Who may chat: an Entra token for the "chess Chat" app (infra/gitops/chat.tf) with scope
    # Chat.Access and app role Chat.User.
    tenant_id: str = ""
    client_id: str = ""  # the chat app registration
    delegated_scope: str = "Chat.Access"
    required_role: str = "Chat.User"

    # The model, as Pydantic AI's provider:model. Its API key comes from the provider's usual
    # variable (ANTHROPIC_API_KEY for anthropic:...).
    model: str = "anthropic:claude-opus-5-5"
    effort: str = "medium"

    # Data: the lakehouse MCP server, signed in to as this pod's workload identity (client
    # credentials) for the MCP app, whose app role Lakehouse.Query that identity holds.
    mcp_url: str = "http://chess-mcp.chess-mcp.svc.cluster.local/mcp"
    mcp_client_id: str = ""  # the MCP app registration (scope api://<it>/.default)

    # Spend: model cost in US dollars per UTC day, for everyone together and per user.
    daily_cap_usd: float = 5.0
    user_daily_cap_usd: float = 5.0
    ledger_path: Path = Path("/data/spend.sqlite3")

    # Per run and per user.
    max_messages: int = 40  # conversation length a request may carry
    max_requests_per_run: int = 12  # model calls in one answer (tool-use loop)
    max_tool_calls_per_run: int = 20
    chats_per_minute: int = 6

    http_port: int = 8000
    log_level: str = "INFO"

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"
