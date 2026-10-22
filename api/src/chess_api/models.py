"""Response models: the OpenAPI contract the dashboard's TypeScript client is generated from."""
from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
# A FEN: piece placement (8 ranks), side to move, castling; en passant and counters optional.
FEN_PATTERN = (r"^([pnbrqkPNBRQK1-8]{1,8}/){7}[pnbrqkPNBRQK1-8]{1,8} [wb] (-|KQ?k?q?|Qk?q?|kq?|q)"
               r"( (-|[a-h][36]))?( \d+ \d+)?$")
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
Month = Annotated[str, Field(pattern=MONTH_PATTERN, examples=["2015-12"], description="YYYY-MM")]


class Speed(StrEnum):
    """Lichess speed classes, as in lake.curated.games."""

    ultrabullet = "ultrabullet"
    bullet = "bullet"
    blitz = "blitz"
    rapid = "rapid"
    classical = "classical"
    correspondence = "correspondence"


class RatingBand(IntEnum):
    """The explorer's rating bands: lower bound of the players' average rating (0 = under 1200)."""

    under_1200 = 0
    from_1200 = 1200
    from_1600 = 1600
    from_2000 = 2000
    from_2400 = 2400


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


class CostRow(BaseModel):
    date: str = Field(description="Usage date, YYYY-MM-DD")
    service: str | None = Field(description="Azure service name")
    cost: float


class Costs(BaseModel):
    """Daily Azure cost of the project, by service (internal: Grafana only, never routed publicly)."""

    from_month: str
    currency: str | None
    rows: list[CostRow]


class ExplorerMove(BaseModel):
    move: str = Field(description="The move in SAN, as played")
    games: int
    white_wins: int
    draws: int
    black_wins: int
    avg_rating: float | None = Field(description="Mean of both players' ratings over the rated games")
    example_game_id: str | None = Field(description="The highest-rated game with this move: https://lichess.org/<id>")
    example_rating: float | None = Field(description="That game's average rating")


class Explorer(BaseModel):
    position: str = Field(
        description="The position looked up: the first three FEN fields (placement, side to move, castling)"
    )
    from_month: Month | None = Field(description="null = from the first month with data")
    to_month: Month | None = Field(description="null = to the last month with data")
    speed: Speed | None = Field(description="null = every speed")
    rating_band: RatingBand | None = Field(description="null = every band")
    games: int = Field(description="Games that continued from this position (sum over the moves)")
    moves: list[ExplorerMove] = Field(
        description="Moves played from the position at least 5 times in a month (first 16 plies), most played first"
    )
