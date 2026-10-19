"""curated.games: one typed row per raw game, built a month at a time from lake.raw.games.

    source  lake.raw.games (main), pinned to one snapshot per build
    table   lake.curated.games   partitioned by month, sorted by game_id

Nothing is filtered: every raw row becomes exactly one curated row. What's unusual is kept
and flagged or counted instead: duplicate game ids (is_duplicate on the extra copies),
unfinished games (result null), ratings outside 400–4000, unknown terminations, games whose
start isn't in the dump's month.

build_month(): write-audit-publish (lichess_spark.wap).
  blocking   curated rows = raw rows, game_id present, time control parseable,
             played_at present, month column consistent, not empty
  counted    game_id unique, rating in range, result decided, termination known,
             played_at in month
Each row records the raw partition snapshot it came from (_raw_snapshot_id), so a month is
rebuilt (--pending) whenever raw republishes it.
"""
from __future__ import annotations

import re
import uuid

from . import raw, wap
from .wap import Check

TABLE = "lake.curated.games"

SITE_PATTERN = r"^https://lichess\.org/([A-Za-z0-9]{8})$"
TIME_CONTROL_PATTERN = r"^([0-9]+)\+([0-9]+)$"
CORRESPONDENCE = "-"
# Lichess's speed classes: estimated duration = base + 40 × increment (seconds), below each bound.
INCREMENT_WEIGHT = 40
SPEED_BOUNDS = (("ultrabullet", 30), ("bullet", 180), ("blitz", 480), ("rapid", 1500))
RESULTS = {"1-0": "white", "0-1": "black", "1/2-1/2": "draw"}  # anything else ("*") → null
TERMINATIONS = ("Normal", "Time forfeit", "Abandoned", "Unterminated", "Insufficient material", "Rules infraction")
RATING_RANGE = (400, 4000)


def speed_class(base: int, increment: int) -> str:
    estimate = base + INCREMENT_WEIGHT * increment
    for name, bound in SPEED_BOUNDS:
        if estimate < bound:
            return name
    return "classical"


def parse_time_control(tc: str | None) -> tuple[int | None, int | None, str | None]:
    """'180+2' → (180, 2, 'blitz'); '-' → (None, None, 'correspondence'); unparseable → all None.
    Reference for the Spark expression in transform() (unit-tested; local_curated checks they agree)."""
    if tc == CORRESPONDENCE:
        return None, None, "correspondence"
    m = re.match(TIME_CONTROL_PATTERN, tc or "")
    if not m:
        return None, None, None
    base, inc = int(m[1]), int(m[2])
    return base, inc, speed_class(base, inc)


def evaluate(month: str, stats: dict, raw_rows: int) -> list[Check]:
    """Turn the branch's stats for one month into checks (pure, unit-tested)."""
    n = lambda key: int(stats.get(key) or 0)  # noqa: E731
    rows = n("rows")
    lo, hi = RATING_RANGE
    return [
        Check("row_count_matches_raw", rows == raw_rows, rows, raw_rows, "every raw row becomes one curated row"),
        Check("game_id_present", n("missing_game_id") == 0, n("missing_game_id"), 0, "Site must be a lichess.org game URL"),
        Check("time_control_parseable", n("bad_time_control") == 0, n("bad_time_control"), 0, "base+increment or '-'"),
        Check("played_at_present", n("missing_played_at") == 0, n("missing_played_at"), 0, "UTCDate + UTCTime"),
        Check("month_column_consistent", n("wrong_month") == 0, n("wrong_month"), 0, f"month column = {month}"),
        Check("not_empty", rows > 0, rows, None),
        Check("game_id_unique", n("duplicates") == 0, n("duplicates"), 0, "extra copies flagged is_duplicate",
              blocking=False),
        Check("rating_in_range", n("rating_out_of_range") == 0, n("rating_out_of_range"), 0,
              f"games with a rating outside {lo}–{hi}", blocking=False),
        Check("result_decided", n("undecided") == 0, n("undecided"), 0, "Result '*' (unfinished): result is null",
              blocking=False),
        Check("termination_known", n("unknown_termination") == 0, n("unknown_termination"), 0,
              "Termination not in " + ", ".join(TERMINATIONS), blocking=False),
        Check("played_at_in_month", n("outside_month") == 0, n("outside_month"), 0,
              "game started outside the dump's month", blocking=False),
    ]


# ---------------------------------------------------------------------------- Spark side

def transform(raw_df, raw_snapshot_id: int | None, run_id: str):
    """Raw rows of one month → curated rows, one for one (no filters)."""
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    def to_int(col):
        return F.col(col).try_cast("int")

    tc = F.col("TimeControl")
    tc_ok = tc.rlike(TIME_CONTROL_PATTERN)
    base = F.when(tc_ok, F.regexp_extract(tc, TIME_CONTROL_PATTERN, 1).try_cast("int"))
    inc = F.when(tc_ok, F.regexp_extract(tc, TIME_CONTROL_PATTERN, 2).try_cast("int"))
    estimate = base + F.lit(INCREMENT_WEIGHT) * inc
    speed = F.when(tc == F.lit(CORRESPONDENCE), F.lit("correspondence"))
    for name, bound in SPEED_BOUNDS:
        speed = speed.when(estimate < bound, F.lit(name))
    speed = speed.when(estimate.isNotNull(), F.lit("classical"))

    game_id = F.nullif(F.regexp_extract("Site", SITE_PATTERN, 1), F.lit(""))
    result = F.lit(None).cast("string")
    for pgn, name in RESULTS.items():
        result = F.when(F.col("Result") == pgn, F.lit(name)).otherwise(result)

    df = raw_df.select(
        game_id.alias("game_id"),
        F.col("month"),
        F.try_to_timestamp(F.concat_ws(" ", "UTCDate", "UTCTime"), F.lit("yyyy.MM.dd HH:mm:ss")).alias("played_at"),
        F.col("Event").alias("event"),
        F.nullif(F.regexp_extract("Event", r"lichess\.org/tournament/([A-Za-z0-9]+)", 1), F.lit("")).alias("tournament_id"),
        F.nullif(F.regexp_extract("Event", r"lichess\.org/swiss/([A-Za-z0-9]+)", 1), F.lit("")).alias("swiss_id"),
        F.col("White").alias("white"),
        F.col("Black").alias("black"),
        to_int("WhiteElo").alias("white_elo"),
        to_int("BlackElo").alias("black_elo"),
        to_int("WhiteRatingDiff").alias("white_rating_diff"),
        to_int("BlackRatingDiff").alias("black_rating_diff"),
        F.col("WhiteTitle").alias("white_title"),
        F.col("BlackTitle").alias("black_title"),
        F.coalesce(F.col("WhiteTitle") == "BOT", F.lit(False)).alias("white_is_bot"),
        F.coalesce(F.col("BlackTitle") == "BOT", F.lit(False)).alias("black_is_bot"),
        result.alias("result"),
        F.col("Termination").alias("termination"),
        F.col("ECO").alias("eco"),
        F.col("Opening").alias("opening"),
        tc.alias("time_control"),
        base.alias("base_seconds"),
        inc.alias("increment_seconds"),
        speed.alias("speed"),
        F.col("_source_file"),
        F.col("_ingested_at"),
    )
    # Keep every copy of a repeated game id; mark all but the first so `NOT is_duplicate`
    # gives one row per game.
    first = Window.partitionBy("game_id").orderBy("_source_file", "_ingested_at")
    is_dup = F.col("game_id").isNotNull() & (F.row_number().over(first) > 1)
    return (df.withColumn("is_duplicate", is_dup)
              .drop("_ingested_at")
              .withColumn("_raw_snapshot_id", F.lit(raw_snapshot_id).cast("long"))
              .withColumn("_run_id", F.lit(run_id))
              .withColumn("_built_at", F.current_timestamp()))


def ensure_tables(spark, sample_df) -> None:
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.curated")
    if not wap.table_exists(spark, TABLE):
        ddl = sample_df._jdf.schema().toDDL()
        spark.sql(f"""
            CREATE TABLE {TABLE} ({ddl})
            USING iceberg
            PARTITIONED BY (month)
            TBLPROPERTIES (
              'format-version' = '2',
              'write.parquet.compression-codec' = 'zstd',
              'write.target-file-size-bytes' = '268435456',
              'comment' = 'One typed row per raw game (nothing filtered; is_duplicate marks repeated game ids). Partitioned by month (YYYY-MM), sorted by game_id.'
            )""")
        spark.sql(f"ALTER TABLE {TABLE} WRITE ORDERED BY game_id")
    wap.ensure_main(spark, TABLE, sample_df.schema)
    wap.ensure_dq_table(spark)


def branch_stats(spark, branch: str, month: str) -> dict:
    lo, hi = RATING_RANGE
    terminations = ", ".join(f"'{t}'" for t in TERMINATIONS)
    out_of_range = lambda c: f"({c} IS NOT NULL AND {c} NOT BETWEEN {lo} AND {hi})"  # noqa: E731
    row = spark.sql(f"""
        SELECT count(*)                                                          AS rows,
               count_if(game_id IS NULL)                                         AS missing_game_id,
               count_if(speed IS NULL)                                           AS bad_time_control,
               count_if(played_at IS NULL)                                       AS missing_played_at,
               count_if(month <> '{month}')                                      AS wrong_month,
               count_if(is_duplicate)                                            AS duplicates,
               count_if({out_of_range('white_elo')} OR {out_of_range('black_elo')}) AS rating_out_of_range,
               count_if(result IS NULL)                                          AS undecided,
               count_if(termination IS NULL OR termination NOT IN ({terminations})) AS unknown_termination,
               count_if(played_at IS NOT NULL AND date_format(played_at, 'yyyy-MM') <> '{month}') AS outside_month
        FROM {TABLE} VERSION AS OF '{branch}'
        WHERE month = '{month}'""").first()
    return row.asDict()


def pending_months(spark) -> list[str]:
    """Raw months not in curated yet, or republished in raw since curated was built."""
    return wap.pending(spark, raw.TABLE, TABLE, "_raw_snapshot_id")


def build_month(spark, month: str, run_id: str | None = None) -> list[Check]:
    """Raw → curated for one month, write-audit-publish. Returns the checks; raises nothing on a failed audit."""
    run_id = run_id or uuid.uuid4().hex
    spark.conf.set("spark.sql.session.timeZone", "UTC")  # played_at is parsed from UTC date and time
    lineage, source = wap.pinned_month(spark, raw.TABLE, month)
    raw_rows = source.count()

    df = transform(source, lineage, run_id)
    ensure_tables(spark, df)
    audit = lambda branch: evaluate(month, branch_stats(spark, branch, month), raw_rows)  # noqa: E731
    return wap.publish(spark, TABLE, month, df, audit, run_id)
