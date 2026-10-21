"""The agent: one model (Pydantic AI, provider:model from config), one toolset (the lakehouse MCP
server, its only way to touch data), and a metered model wrapper that charges every model request
to the user's daily spend as it happens, also when a stream is cut off."""
from __future__ import annotations

import contextvars
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse, infer_model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from .auth import EntraIdentityToken, WorkloadIdentityAuth
from .ledger import Ledger
from .pricing import cost_usd, price_of
from .settings import Settings

log = logging.getLogger("chess_chat")
spend_log = logging.getLogger("chess_chat.spend")

# Who the current run is for: the metered model charges this user.
current_user: contextvars.ContextVar[str] = contextvars.ContextVar("current_user", default="anonymous")

INSTRUCTIONS = """\
You answer questions about chess games played on Lichess, using the Lichess lakehouse: rated standard games from the Lichess open database (database.lichess.org), as tables you query with the tools.

How to work:
- Look before you guess: use `list_tables`, `describe_table` and the `lake://docs/tables` resource to see what exists, then `run_sql` (Trino SQL, one SELECT, tables written as schema.table, e.g. gold.monthly_summary).
- Prefer the small gold tables; use curated.* only when the question needs single games or players, and then always filter on `month` (YYYY-MM).
- Check which months exist before answering about a period; say plainly when the data doesn't cover what was asked.
- Count each game once (`NOT is_duplicate` on curated tables). Don't add up distinct-player counts across months or speeds.

How to answer:
- Lead with the answer, in a sentence or two, with the actual numbers. Use a small markdown table when comparing several values.
- Say which months the numbers cover, and briefly how you got them (the table, and the SQL if it helps the reader check).
- If a query fails, read the error, fix the SQL and try again rather than guessing.
- Stay on chess and this data. If asked for something else, say what you can help with instead.
"""


class MeteredModel(WrapperModel):
    """Charges each model request to `current_user` in the ledger, from the usage the provider
    reports, as soon as the request ends: complete, failed or cut off mid-stream."""

    def __init__(self, wrapped: Model, price_key: str, ledger: Ledger):
        super().__init__(wrapped)
        self.price_key = price_key  # provider:model, the key into the price table
        self.ledger = ledger

    def charge(self, usage: Any) -> float:
        try:
            return self._charge(usage)
        except Exception:  # a failure inside a stream would otherwise vanish; the cap depends on this
            log.exception("could not record spend", extra={"model": self.price_key})
            raise

    def _charge(self, usage: Any) -> float:
        usd = cost_usd(self.price_key, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                       cache_read_tokens=usage.cache_read_tokens, cache_write_tokens=usage.cache_write_tokens)
        oid = current_user.get()
        self.ledger.add(oid, usd)
        spend_log.info("model request", extra={"oid": oid, "model": self.price_key, "usd": round(usd, 6),
                                               "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                                               "cache_read_tokens": usage.cache_read_tokens})
        return usd

    async def request(self, messages, model_settings, model_request_parameters: ModelRequestParameters):
        response = await self.wrapped.request(messages, model_settings, model_request_parameters)
        self.charge(response.usage)
        return response

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters: ModelRequestParameters,
                             run_context=None) -> AsyncGenerator[StreamedResponse]:
        async with self.wrapped.request_stream(messages, model_settings, model_request_parameters,
                                               run_context) as stream:
            try:
                yield stream
            finally:
                self.charge(stream.usage)


def model_settings_for(settings: Settings) -> ModelSettings:
    """Provider-specific settings for the configured model."""
    if settings.model.startswith("anthropic:"):
        from pydantic_ai.models.anthropic import AnthropicModelSettings

        return AnthropicModelSettings(
            max_tokens=16_000,
            # Adaptive thinking, summarized so the chat can show how it reasoned.
            anthropic_thinking={"type": "adaptive", "display": "summarized"},
            anthropic_effort=settings.effort,
            anthropic_cache_instructions=True,
            anthropic_cache_tool_definitions=True,
            # If a safety classifier declines, the API retries on a suitable fallback model.
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        )
    return ModelSettings(max_tokens=16_000)


def build_model(settings: Settings) -> Model:
    """The configured model. Claude signs in with Workload Identity Federation when the rule is
    set; any other provider:model (or Claude with ANTHROPIC_API_KEY) the way Pydantic AI does."""
    provider, _, name = settings.model.partition(":")
    if provider != "anthropic" or not settings.anthropic_federated:
        return infer_model(settings.model)
    from anthropic import AsyncAnthropic, WorkloadIdentityCredentials
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    missing = [f for f in ("claude_audience_client_id", "anthropic_organization_id", "anthropic_service_account_id")
               if not getattr(settings, f)]
    if missing:
        raise ValueError(f"Anthropic federation needs {', '.join(missing)}")
    credentials = WorkloadIdentityCredentials(
        identity_token_provider=EntraIdentityToken(f"api://{settings.claude_audience_client_id}/.default"),
        federation_rule_id=settings.anthropic_federation_rule_id,
        organization_id=settings.anthropic_organization_id,
        service_account_id=settings.anthropic_service_account_id,
        workspace_id=settings.anthropic_workspace_id or None,
    )
    # Explicit credentials outrank ANTHROPIC_API_KEY, so a stray key can't take over.
    client = AsyncAnthropic(credentials=credentials)
    return AnthropicModel(name, provider=AnthropicProvider(anthropic_client=client))


def build_agent(settings: Settings, ledger: Ledger, *, model: Model | None = None,
                toolsets: list | None = None, auth_factory: Callable[[str], Any] | None = None) -> Agent:
    price_of(settings.model)  # refuse to start with a model whose spend can't be capped
    if toolsets is None:
        auth = (auth_factory or WorkloadIdentityAuth)(f"api://{settings.mcp_client_id}/.default")
        toolsets = [MCPToolset(settings.mcp_url, auth=auth, include_instructions=True, tool_error_behavior="retry")]
    metered = MeteredModel(model or build_model(settings), settings.model, ledger)
    return Agent(metered, instructions=INSTRUCTIONS, toolsets=toolsets, model_settings=model_settings_for(settings),
                 retries=3)
