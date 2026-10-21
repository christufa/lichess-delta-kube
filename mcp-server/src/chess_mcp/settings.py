"""Configuration from the environment (CHESS_MCP_*). Deployment-specific values (tenant, client
ID) come from the cluster facts through the Helm chart, never from git."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHESS_MCP_")

    # Public address: the protected resource (RFC 9728) MCP clients connect to; the MCP
    # endpoint is <public_url>/mcp.
    public_url: str = "https://chess-mcp.generops.dev"

    # Entra ID: the "chess MCP" app registration (infra/gitops/mcp.tf).
    tenant_id: str = ""
    client_id: str = ""
    app_id_uri: str = ""  # default api://<client_id>; the scope clients ask for is <app_id_uri>/<role>
    required_role: str = "Lakehouse.Query"  # app role: the user is assigned
    delegated_scope: str = "MCP.Access"  # scope the client asks for (Entra needs it to differ from the role)

    # Trino, as the read-only user chess-mcp (lake.curated and lake.gold only).
    trino_host: str = "trino.trino.svc.cluster.local"
    trino_port: int = 8443
    trino_user: str = "chess-mcp"
    trino_catalog: str = "lake"
    trino_token_file: Path = Path("/var/run/trino/token")
    trino_ca_file: Path = Path("/ca/ca.crt")  # ConfigMap internal-ca (trust-manager)
    query_timeout_seconds: int = 30
    max_rows: int = 1000

    # Per user (Entra object id): queries per minute.
    queries_per_minute: int = 30

    # Not `port`: Kubernetes injects CHESS_MCP_PORT=tcp://… for a Service named chess-mcp.
    http_port: int = 8000
    log_level: str = "INFO"

    @property
    def scope(self) -> str:
        """The OAuth scope a client requests and this server requires."""
        uri = self.app_id_uri or f"api://{self.client_id}"
        return f"{uri}/{self.delegated_scope}"

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"
