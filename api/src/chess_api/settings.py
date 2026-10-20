"""Configuration from the environment (prefix CHESS_API_). Defaults match the cluster."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHESS_API_")

    # Trino: JWT sign-in with this pod's projected service account token (audience trino).
    # The user must match the token's principal (the service account name), or Trino treats
    # the request as impersonation and denies it.
    trino_host: str = "trino.trino.svc.cluster.local"
    trino_port: int = 8443
    trino_user: str = "chess-api"
    trino_catalog: str = "lake"
    trino_schema: str = "gold"
    trino_token_file: Path = Path("/var/run/trino/token")
    trino_ca_file: Path = Path("/ca/ca.crt")  # ConfigMap internal-ca (trust-manager)
    trino_timeout_seconds: float = 30.0

    # Gold changes once a month, so results are kept for an hour in each replica.
    cache_ttl_seconds: int = 3600
    cache_max_entries: int = 2048
    cache_control: str = "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400"

    # Token bucket per client IP (per replica): `rate_limit_burst` requests at once,
    # refilled at `rate_limit_per_second`. 0 turns it off.
    rate_limit_per_second: float = 5.0
    rate_limit_burst: int = 60
    # cloudflared forwards the visitor's address in this header; only cloudflared may
    # connect to the pods (CiliumNetworkPolicy), so it can't be spoofed from elsewhere.
    client_ip_header: str = "cf-connecting-ip"

    # /readyz re-checks Trino at most this often.
    ready_check_seconds: float = 30.0

    log_level: str = "INFO"
