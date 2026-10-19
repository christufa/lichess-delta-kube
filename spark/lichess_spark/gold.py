"""Gold tables for the dashboard and chat: small monthly aggregates of the curated tables.

    lake.gold.monthly_summary      month × speed ('all' = every speed): games, players, results,
                                   bot games, average rating
    lake.gold.openings             month × speed × ECO/opening × rating band (200 wide, by the
                                   players' average): games and results
    lake.gold.rating_distribution  month × speed × rating bucket (50 wide): player-games, players

Each game counts once: rows flagged is_duplicate in curated are left out here. Nothing else
is: unfinished games count as games (`unfinished`), unknown ratings form a null band/bucket.

build_month(): each table gets write-audit-publish (lichess_spark.wap) with blocking checks
that its totals reconcile with curated (#27): games add up to the month's unique games
(player-games to twice that), and the table isn't empty. The month's source is pinned to one
player_games snapshot (recorded as _player_games_snapshot_id), so --pending rebuilds a month
whenever player_games changes.
"""
from __future__ import annotations

import uuid

from . import curated, player_games, wap
from .wap import Check

SUMMARY = "lake.gold.monthly_summary"
OPENINGS = "lake.gold.openings"
RATINGS = "lake.gold.rating_distribution"
TABLES = (SUMMARY, OPENINGS, RATINGS)
LINEAGE = "_player_games_snapshot_id"
ALL_SPEEDS = "all"
RATING_BAND = 200
RATING_BUCKET = 50
COMMENTS = {
    SUMMARY: "Per month and speed (speed all = every speed): games, distinct players, results, bot games, average rating. Each game once.",
    OPENINGS: f"Per month, speed, ECO/opening and rating band ({RATING_BAND} wide, players' average rating): games and results. Each game once.",
    RATINGS: f"Per month, speed and rating bucket ({RATING_BUCKET} wide): player-games and distinct players. Each game once.",
}


def evaluate(table: str, stats: dict, unique_games: int) -> list[Check]:
    """Reconciliation checks for one gold table and month (pure, unit-tested)."""
    n = lambda key: int(stats.get(key) or 0)  # noqa: E731
    rows = n("rows")
    if table == SUMMARY:
        return [
            Check("total_matches_curated", n("total_games") == unique_games, n("total_games"), unique_games,
                  f"speed '{ALL_SPEEDS}' = unique curated games"),
            Check("speeds_add_up", n("speed_games") == unique_games, n("speed_games"), unique_games),
            Check("not_empty", rows > 0, rows, None),
        ]
    if table == OPENINGS:
        return [
            Check("games_add_up", n("games") == unique_games, n("games"), unique_games),
            Check("not_empty", rows > 0, rows, None),
        ]
    if table == RATINGS:
        return [
            Check("player_games_add_up", n("player_games") == 2 * unique_games, n("player_games"), 2 * unique_games),
            Check("not_empty", rows > 0, rows, None),
        ]
    raise ValueError(table)


# ---------------------------------------------------------------------------- Spark side

def _results(F):
    return [
        F.count_if(F.col("result") == "white").alias("white_wins"),
        F.count_if(F.col("result") == "draw").alias("draws"),
        F.count_if(F.col("result") == "black").alias("black_wins"),
        F.count_if(F.col("result").isNull()).alias("unfinished"),
    ]


def summary(games, players):
    from pyspark.sql import functions as F

    def agg(df):
        return df.agg(
            F.count("*").alias("games"),
            *_results(F),
            F.count_if(F.col("white_is_bot") | F.col("black_is_bot")).alias("bot_games"),
            F.avg((F.col("white_elo") + F.col("black_elo")) / 2).alias("avg_rating"),
        )

    by_speed = agg(games.groupBy("month", "speed"))
    total = agg(games.groupBy("month")).withColumn("speed", F.lit(ALL_SPEEDS))
    distinct = lambda df: df.agg(F.countDistinct("player_id").alias("players"))  # noqa: E731
    players_by = distinct(players.groupBy("month", "speed")).unionByName(
        distinct(players.groupBy("month")).withColumn("speed", F.lit(ALL_SPEEDS)))
    return (by_speed.unionByName(total)
            .join(players_by, ["month", "speed"], "left")
            .select("month", "speed", "games", "players", "white_wins", "draws", "black_wins", "unfinished",
                    "bot_games", "avg_rating"))


def openings(games):
    from pyspark.sql import functions as F

    band = (F.floor((F.col("white_elo") + F.col("black_elo")) / 2 / RATING_BAND) * RATING_BAND).cast("int")
    return (games.withColumn("rating_band", band)
            .groupBy("month", "speed", "eco", "opening", "rating_band")
            .agg(F.count("*").alias("games"), *_results(F)))


def rating_distribution(players):
    from pyspark.sql import functions as F

    bucket = (F.floor(F.col("rating") / RATING_BUCKET) * RATING_BUCKET).cast("int")
    return (players.withColumn("rating_bucket", bucket)
            .groupBy("month", "speed", "rating_bucket")
            .agg(F.count("*").alias("player_games"), F.countDistinct("player_id").alias("players")))


def ensure_table(spark, table: str, sample_df) -> None:
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.gold")
    if not wap.table_exists(spark, table):
        ddl = sample_df._jdf.schema().toDDL()
        spark.sql(f"""
            CREATE TABLE {table} ({ddl})
            USING iceberg
            PARTITIONED BY (month)
            TBLPROPERTIES ('format-version' = '2', 'write.parquet.compression-codec' = 'zstd',
                           'comment' = '{COMMENTS[table]}')""")
    wap.ensure_main(spark, table, sample_df.schema)
    wap.ensure_dq_table(spark)


def branch_stats(spark, table: str, branch: str, month: str) -> dict:
    measures = {
        SUMMARY: f"sum(CASE WHEN speed = '{ALL_SPEEDS}' THEN games END) AS total_games, "
                 f"sum(CASE WHEN speed <> '{ALL_SPEEDS}' THEN games END) AS speed_games",
        OPENINGS: "sum(games) AS games",
        RATINGS: "sum(player_games) AS player_games",
    }[table]
    return spark.sql(f"""
        SELECT count(*) AS rows, {measures}
        FROM {table} VERSION AS OF '{branch}'
        WHERE month = '{month}'""").first().asDict()


def pending_months(spark) -> list[str]:
    """player_games months that any gold table hasn't been built from yet."""
    return sorted(set().union(*(wap.pending(spark, player_games.TABLE, t, LINEAGE) for t in TABLES)))


def build_month(spark, month: str, run_id: str | None = None) -> list[Check]:
    """curated → the three gold tables for one month, each with write-audit-publish."""
    from pyspark.sql import functions as F

    run_id = run_id or uuid.uuid4().hex
    lineage, players = wap.pinned_month(spark, player_games.TABLE, month)
    _, games = wap.pinned_month(spark, curated.TABLE, month)
    games, players = games.where("NOT is_duplicate").cache(), players.where("NOT is_duplicate")
    unique_games = games.count()

    built = {SUMMARY: summary(games, players), OPENINGS: openings(games), RATINGS: rating_distribution(players)}
    checks = []
    for table, df in built.items():
        df = (df.withColumn(LINEAGE, F.lit(lineage).cast("long"))
                .withColumn("_run_id", F.lit(run_id))
                .withColumn("_built_at", F.current_timestamp()))
        ensure_table(spark, table, df)
        audit = lambda branch, t=table: evaluate(t, branch_stats(spark, t, branch, month), unique_games)  # noqa: E731
        checks += wap.publish(spark, table, month, df, audit, run_id)
    games.unpersist()
    return checks
