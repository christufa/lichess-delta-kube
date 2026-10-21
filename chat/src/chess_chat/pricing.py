"""Model prices for the spend cap. A model without a price here can't be used: the cap couldn't be
enforced. Prices are US dollars per million tokens (Anthropic's list prices, 2026-10)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute cache writes


PRICES: dict[str, Price] = {
    "anthropic:claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
    "anthropic:claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
    "anthropic:claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
}


class UnknownPrice(LookupError):
    """No price for this model, so its spend can't be capped."""


def price_of(model: str) -> Price:
    try:
        return PRICES[model]
    except KeyError as e:
        raise UnknownPrice(f"no price for {model!r}; add it to chess_chat.pricing.PRICES") from e


def cost_usd(model: str, *, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0,
             cache_write_tokens: int = 0) -> float:
    """Cost of one model request. `input_tokens` is the total input, cached parts included (Pydantic
    AI's convention), so the cached parts are taken out before charging the base input price."""
    p = price_of(model)
    uncached = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
    return (uncached * p.input + cache_read_tokens * p.cache_read + cache_write_tokens * p.cache_write
            + output_tokens * p.output) / 1_000_000
