"""Response models: the OpenAPI contract the dashboard's TypeScript client is generated from."""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
Month = Annotated[str, Field(pattern=MONTH_PATTERN, examples=["2015-12"], description="YYYY-MM")]


class Speed(StrEnum):
    """Lichess speed classes, as in lake.curated.games."""

    ultrabullet = "ultrabullet"
    bullet = "bullet"
    blitz = "blitz"
    rapid = "rapid"
    classical = "classical"
    correspondence = "correspondence"


class SummarySpeed(StrEnum):
    """Speeds in the monthly summary: every speed class, plus `all` (every game once)."""

    all = "all"
    ultrabullet = "ultrabullet"
    bullet = "bullet"
    blitz = "blitz"
    rapid = "rapid"
    classical = "classical"
    correspondence = "correspondence"


class Results(BaseModel):
    white_wins: int
    draws: int
    black_wins: int
    unfinished: int = Field(description="Games without a decided result")


class Months(BaseModel):
    months: list[Month] = Field(description="Months with data, oldest first")


class SummaryRow(Results):
    month: Month
    speed: str | None = Field(description="Speed class, `all`, or null for an unparseable time control")
    games: int
    players: int = Field(description="Distinct players")
    bot_games: int = Field(description="Games with a bot on either side")
    avg_rating: float | None = Field(description="Mean of both players' ratings")


class Summary(BaseModel):
    from_month: Month
    to_month: Month
    rows: list[SummaryRow] = Field(description="Ordered by month, then speed")


class RatingBucket(BaseModel):
    speed: str | None
    rating_bucket: int | None = Field(description="Lower bound of a 50-point bucket; null = unrated")
    player_games: int = Field(description="Games counted once per player")
    players: int = Field(description="Distinct players")


class RatingDistribution(BaseModel):
    month: Month
    bucket_width: int = 50
    rows: list[RatingBucket] = Field(description="Ordered by speed, then bucket")


class Opening(Results):
    eco: str | None
    opening: str | None
    games: int


class Openings(BaseModel):
    from_month: Month
    to_month: Month
    speed: Speed | None = Field(description="null = every speed")
    rating_band: int | None = Field(
        description="Lower bound of the 200-point band (players' average rating); null = every band"
    )
    band_width: int = 200
    total_games: int = Field(description="Games in the selection, across every opening")
    openings: list[Opening] = Field(description="Most played first")


class Problem(BaseModel):
    detail: str


class Health(BaseModel):
    status: str
