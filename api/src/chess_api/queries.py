"""SQL over lake.gold only (chess-api can't read anything else). Each game is counted once
in gold, so sums across months, speeds and bands are game counts; distinct players are not
summed (they don't add up), which is why those come per month and speed as stored.

Values always go in as parameters; the SQL text is built from constants only.
"""
from __future__ import annotations

from .models import (
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
