"""curated.player_games: two rows per curated game, one from each player's side.

    source  lake.curated.games (main), pinned to one snapshot per build
    table   lake.curated.player_games   partitioned by (month, bucket(16, player_id)),
                                        sorted by player_id, played_at (ADR 0001)

Like curated.games, nothing is filtered: every game gives exactly one white and one black
row, duplicates included (is_duplicate is carried over). `player_id` is the lower-cased
username (Lichess usernames are case-insensitive); `player` keeps it as written.

build_month(): write-audit-publish (lichess_spark.wap).
  blocking   rows = 2 × games, one white and one black row per game, player present,
             month column consistent, not empty
  counted    outcome decided (unfinished games have outcome null)
"""
from __future__ import annotations

import uuid

from . import curated, wap
from .wap import Check

TABLE = "lake.curated.player_games"
BUCKETS = 16  # ADR 0001
OUTCOMES = {"white": ("win", "loss"), "black": ("loss", "win"), "draw": ("draw", "draw")}  # result → (white, black)
SCORES = {"win": 1.0, "draw": 0.5, "loss": 0.0}


def evaluate(month: str, stats: dict, games: int) -> list[Check]:
    """Turn the branch's stats for one month into checks (pure, unit-tested)."""
    n = lambda key: int(stats.get(key) or 0)  # noqa: E731
    rows = n("rows")
    return [
        Check("two_rows_per_game", rows == 2 * games, rows, 2 * games, "every curated game gives two rows"),
        Check("one_white_row_per_game", n("white_rows") == games, n("white_rows"), games),
        Check("one_black_row_per_game", n("black_rows") == games, n("black_rows"), games),
        Check("player_present", n("missing_player") == 0, n("missing_player"), 0),
        Check("month_column_consistent", n("wrong_month") == 0, n("wrong_month"), 0, f"month column = {month}"),
        Check("not_empty", rows > 0, rows, None),
        Check("outcome_decided", n("undecided") == 0, n("undecided"), 0, "unfinished games: outcome is null",
              blocking=False),
    ]


# ---------------------------------------------------------------------------- Spark side

def transform(games_df, games_snapshot_id: int | None, run_id: str):
    """Curated games of one month → two rows per game (no filters)."""
    from pyspark.sql import functions as F

    def outcome(side: int):
        out = F.lit(None).cast("string")
        for result, sides in OUTCOMES.items():
            out = F.when(F.col("result") == result, F.lit(sides[side])).otherwise(out)
        return out

    def score(col):
        out = F.lit(None).cast("double")
        for name, value in SCORES.items():
            out = F.when(col == name, F.lit(value)).otherwise(out)
        return out

    shared = ["game_id", "month", "played_at", "speed", "base_seconds", "increment_seconds", "eco", "opening",
              "termination", "tournament_id", "swiss_id", "is_duplicate"]

    def side(color: str, other: str, index: int):
        return games_df.select(
            F.col(color).alias("player"),
            F.lower(F.col(color)).alias("player_id"),
            F.lit(color).alias("color"),
            F.col(other).alias("opponent"),
            F.lower(F.col(other)).alias("opponent_id"),
            F.col(f"{color}_elo").alias("rating"),
            F.col(f"{other}_elo").alias("opponent_rating"),
            F.col(f"{color}_rating_diff").alias("rating_diff"),
            F.col(f"{color}_title").alias("title"),
            F.col(f"{other}_title").alias("opponent_title"),
            F.col(f"{color}_is_bot").alias("is_bot"),
            F.col(f"{other}_is_bot").alias("opponent_is_bot"),
            outcome(index).alias("outcome"),
            *shared,
        )

    both = side("white", "black", 0).unionByName(side("black", "white", 1))
    return (both.withColumn("score", score(F.col("outcome")))
                .withColumn("_games_snapshot_id", F.lit(games_snapshot_id).cast("long"))
                .withColumn("_run_id", F.lit(run_id))
                .withColumn("_built_at", F.current_timestamp()))


def ensure_tables(spark, sample_df) -> None:
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.curated")
    if not wap.table_exists(spark, TABLE):
        ddl = sample_df._jdf.schema().toDDL()
        spark.sql(f"""
            CREATE TABLE {TABLE} ({ddl})
            USING iceberg
            PARTITIONED BY (month, bucket({BUCKETS}, player_id))
            TBLPROPERTIES (
              'format-version' = '2',
              'write.parquet.compression-codec' = 'zstd',
              'write.target-file-size-bytes' = '268435456',
              'comment' = 'Two rows per curated game, one per player (player_id = lower-cased username). Partitioned by month and bucket({BUCKETS}, player_id), sorted by player_id, played_at.'
            )""")
        spark.sql(f"ALTER TABLE {TABLE} WRITE ORDERED BY player_id, played_at")
    wap.ensure_main(spark, TABLE, sample_df.schema)
    wap.ensure_dq_table(spark)


def branch_stats(spark, branch: str, month: str) -> dict:
    row = spark.sql(f"""
        SELECT count(*)                                AS rows,
               count_if(color = 'white')               AS white_rows,
               count_if(color = 'black')               AS black_rows,
               count_if(player_id IS NULL OR player_id = '') AS missing_player,
               count_if(month <> '{month}')            AS wrong_month,
               count_if(outcome IS NULL)               AS undecided
        FROM {TABLE} VERSION AS OF '{branch}'
        WHERE month = '{month}'""").first()
    return row.asDict()


def pending_months(spark) -> list[str]:
    """Curated game months not in player_games yet, or rebuilt in curated.games since."""
    return wap.pending(spark, curated.TABLE, TABLE, "_games_snapshot_id")


def build_month(spark, month: str, run_id: str | None = None) -> list[Check]:
    """curated.games → player_games for one month, write-audit-publish."""
    run_id = run_id or uuid.uuid4().hex
    lineage, source = wap.pinned_month(spark, curated.TABLE, month)
    games = source.count()
    df = transform(source, lineage, run_id)
    ensure_tables(spark, df)
    audit = lambda branch: evaluate(month, branch_stats(spark, branch, month), games)  # noqa: E731
    return wap.publish(spark, TABLE, month, df, audit, run_id)
