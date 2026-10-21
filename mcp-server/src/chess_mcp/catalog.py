"""What the tools say about the lake: table lists, columns, and the docs resources."""
from __future__ import annotations

import re

from .trino_client import QueryRunner

TABLE_NAME = re.compile(r"^(curated|gold)\.[a-z_][a-z0-9_]*$")

TABLE_DOCS = """\
# The Lichess lakehouse: tables you can query

Rated standard games from the Lichess open database (database.lichess.org). Months available:
call `run_sql` with `SELECT DISTINCT month FROM gold.monthly_summary ORDER BY 1`. Every table
is partitioned by `month` ('YYYY-MM'); filter on it whenever you can, it makes queries fast.
Always write tables as `schema.table` (the catalog is `lake`).

## gold: small aggregates (prefer these)
- `gold.monthly_summary`: month × speed (speed 'all' = every speed). games, players, white_wins,
  draws, black_wins, unfinished, bot_games, avg_rating. Each game counted once. Don't add up
  `players` across speeds or months (one player plays several).
- `gold.openings`: month × speed × eco × opening × rating_band (200-point band of the two
  players' average rating; null when a rating is unknown). games and results.
- `gold.rating_distribution`: month × speed × rating_bucket (50-point). player_games (each game
  counts once per player, at their rating then; these add up) and players (distinct per bucket;
  these don't add up across buckets).

## curated: one row per game / per player (large: hundreds of millions of rows)
- `curated.games`: game_id (8 characters, lichess.org/<game_id>), month, played_at (UTC),
  event, tournament_id, swiss_id, white, black, white_elo, black_elo, white_rating_diff,
  black_rating_diff, white_title, black_title, white_is_bot, black_is_bot, result ('white' |
  'black' | 'draw' | null for unfinished), termination, eco, opening, time_control,
  base_seconds, increment_seconds, speed ('ultrabullet' | 'bullet' | 'blitz' | 'rapid' |
  'classical' | 'correspondence'), is_duplicate. Use `NOT is_duplicate` to count games once.
- `curated.player_games`: two rows per game, one per player: player, player_id (lower-case
  username), color, opponent, opponent_id, rating, opponent_rating, rating_diff, title,
  opponent_title, is_bot, opponent_is_bot, outcome ('win' | 'draw' | 'loss' | null), score
  (1, 0.5, 0), plus the game's game_id, month, played_at, speed, eco, opening, termination,
  is_duplicate. Partitioned by month and by player_id (16 buckets): filtering on a single
  player_id is fast.

The moves themselves aren't available here.
"""

EXAMPLES = """\
# Example questions and the SQL that answers them

- How many games were played in September 2026, by speed?
  `SELECT speed, games FROM gold.monthly_summary WHERE month = '2026-09' ORDER BY games DESC`
- What are the most popular blitz openings at 1800–1999?
  `SELECT eco, opening, sum(games) AS games FROM gold.openings WHERE speed = 'blitz' AND rating_band = 1800 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10`
- How did white do in classical games over time?
  `SELECT month, round(100.0 * white_wins / games, 1) AS white_pct FROM gold.monthly_summary WHERE speed = 'classical' ORDER BY month`
- How many games did a player play in 2015, and how did they score?
  `SELECT count(*) AS games, sum(score) AS points FROM curated.player_games WHERE player_id = 'some-user' AND month BETWEEN '2015-01' AND '2015-12' AND NOT is_duplicate`
- Which bots played the most games in September 2026?
  `SELECT player, count(*) AS games FROM curated.player_games WHERE month = '2026-09' AND is_bot AND NOT is_duplicate GROUP BY 1 ORDER BY 2 DESC LIMIT 10`
"""


def list_tables(db: QueryRunner) -> list[dict[str, str]]:
    names, rows = db.query(
        "SELECT t.table_schema, t.table_name, c.comment "
        "FROM lake.information_schema.tables t "
        "LEFT JOIN system.metadata.table_comments c "
        "  ON c.catalog_name = 'lake' AND c.schema_name = t.table_schema AND c.table_name = t.table_name "
        "WHERE t.table_schema IN ('curated', 'gold') ORDER BY 1, 2"
    )
    return [{"table": f"{r[0]}.{r[1]}", "comment": r[2] or ""} for r in rows]


def describe_table(db: QueryRunner, table: str, sample_rows: int = 5) -> dict:
    table = table.strip().lower().removeprefix("lake.")
    if not TABLE_NAME.match(table):
        raise ValueError("table must be curated.<name> or gold.<name>")
    known = {t["table"] for t in list_tables(db)}
    if table not in known:
        raise ValueError(f"no table {table!r}; available: {', '.join(sorted(known))}")
    _, cols = db.query(f"SHOW COLUMNS FROM lake.{table}")
    names, rows = db.query(f"SELECT * FROM lake.{table} LIMIT {int(sample_rows)}")
    return {
        "table": table,
        "columns": [{"name": c[0], "type": c[1], "comment": c[3] if len(c) > 3 else ""} for c in cols],
        "sample": {"columns": names, "rows": rows},
    }
