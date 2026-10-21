"""SQL over lake.gold, plus lake.ops.azure_cost for the internal cost endpoint (chess-api can't
read anything else: deploy/apps/trino.yaml). Each game is counted once
in gold, so sums across months, speeds and bands are game counts; distinct players are not
summed (they don't add up), which is why those come per month and speed as stored.

Values always go in as parameters; the SQL text is built from constants only.
"""
from __future__ import annotations

from .models import (
    CostRow,
    Costs,
    Months,
    Opening,
    Openings,
    RatingBucket,
    RatingDistribution,
    Speed,
    Summary,
    SummaryRow,
    SummarySpeed,
)
from .trino_client import QueryRunner

SUMMARY = "lake.gold.monthly_summary"
OPENINGS = "lake.gold.openings"
RATINGS = "lake.gold.rating_distribution"
COSTS = "lake.ops.azure_cost"


def months(db: QueryRunner) -> Months:
    rows = db.query(f"SELECT DISTINCT month FROM {SUMMARY} WHERE speed = 'all' ORDER BY month")
    return Months(months=[r["month"] for r in rows])


def summary(db: QueryRunner, from_month: str, to_month: str, speeds: list[SummarySpeed]) -> Summary:
    sql = f"""
        SELECT month, speed, games, players, white_wins, draws, black_wins, unfinished,
               bot_games, avg_rating
        FROM {SUMMARY}
        WHERE month BETWEEN ? AND ?"""
    params: list[object] = [from_month, to_month]
    if speeds:
        sql += f" AND speed IN ({', '.join('?' for _ in speeds)})"
        params += [s.value for s in speeds]
    sql += " ORDER BY month, speed NULLS LAST"
    rows = db.query(sql, params)
    return Summary(from_month=from_month, to_month=to_month, rows=[SummaryRow(**r) for r in rows])


def rating_distribution(db: QueryRunner, month: str, speeds: list[Speed]) -> RatingDistribution:
    sql = f"""
        SELECT speed, rating_bucket, player_games, players
        FROM {RATINGS}
        WHERE month = ?"""
    params: list[object] = [month]
    if speeds:
        sql += f" AND speed IN ({', '.join('?' for _ in speeds)})"
        params += [s.value for s in speeds]
    sql += " ORDER BY speed NULLS LAST, rating_bucket NULLS FIRST"
    rows = db.query(sql, params)
    return RatingDistribution(month=month, rows=[RatingBucket(**r) for r in rows])


def openings(
    db: QueryRunner,
    from_month: str,
    to_month: str,
    speed: Speed | None,
    rating_band: int | None,
    limit: int,
) -> Openings:
    where = "month BETWEEN ? AND ?"
    params: list[object] = [from_month, to_month]
    if speed is not None:
        where += " AND speed = ?"
        params.append(speed.value)
    if rating_band is not None:
        where += " AND rating_band = ?"
        params.append(rating_band)
    # One pass: the top openings plus the selection's total (a window over the grouped rows).
    sql = f"""
        SELECT eco, opening, games, white_wins, draws, black_wins, unfinished, total_games
        FROM (
            SELECT eco, opening,
                   sum(games) AS games, sum(white_wins) AS white_wins, sum(draws) AS draws,
                   sum(black_wins) AS black_wins, sum(unfinished) AS unfinished,
                   sum(sum(games)) OVER () AS total_games
            FROM {OPENINGS}
            WHERE {where}
            GROUP BY eco, opening
        )
        ORDER BY games DESC, eco NULLS LAST, opening NULLS LAST
        LIMIT {int(limit)}"""
    rows = db.query(sql, params)
    total = int(rows[0]["total_games"]) if rows else 0
    return Openings(
        from_month=from_month,
        to_month=to_month,
        speed=speed,
        rating_band=rating_band,
        total_games=total,
        openings=[Opening(**{k: v for k, v in r.items() if k != "total_games"}) for r in rows],
    )


def costs(db: QueryRunner, from_month: str) -> Costs:
    """Daily cost by service from `from_month` on (filtered on the month string, so the SQL is the
    same in Trino and the tests' SQLite)."""
    rows = db.query(
        f"""
        SELECT usage_date, service_name, SUM(cost) AS cost, MAX(currency) AS currency
        FROM {COSTS}
        WHERE month >= ?
        GROUP BY usage_date, service_name
        ORDER BY usage_date, service_name""",
        [from_month],
    )
    currencies = {r["currency"] for r in rows if r["currency"]}
    return Costs(
        from_month=from_month,
        currency=currencies.pop() if len(currencies) == 1 else None,
        rows=[CostRow(date=str(r["usage_date"]), service=r["service_name"], cost=round(r["cost"] or 0.0, 4))
              for r in rows],
    )
