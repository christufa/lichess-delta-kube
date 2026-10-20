"""raw.games: landed Parquet → Iceberg, one month at a time, with write-audit-publish.

    staging  <root>/raw/games/month=YYYY-MM/*.parquet   written by lichess-ingest
    manifest <root>/_state/raw_games/YYYY-MM.json         {"month": …, "games": N, …}
    table    lake.raw.games                               partitioned by month

publish_month(): write-audit-publish (lichess_spark.wap) with these checks, all blocking:
  row count = manifest, Site present, Result valid, month column consistent, not empty.
"""
from __future__ import annotations

import uuid

from . import wap
from .wap import DQ_TABLE, Check, branch_name  # noqa: F401  (re-exported for jobs and tests)

TABLE = "lake.raw.games"
RESULTS = ("1-0", "0-1", "1/2-1/2", "*")


def evaluate(month: str, stats: dict, expected_games: int | None) -> list[Check]:
    """Turn the branch's stats for one month into pass/fail checks (pure, unit-tested)."""
    rows = int(stats.get("rows") or 0)
    checks = [
        Check("row_count_matches_manifest", expected_games is not None and rows == expected_games, rows, expected_games,
              "no manifest" if expected_games is None else ""),
        Check("site_present", int(stats.get("missing_site") or 0) == 0, int(stats.get("missing_site") or 0), 0),
        Check("result_valid", int(stats.get("bad_result") or 0) == 0, int(stats.get("bad_result") or 0), 0,
              "Result must be one of " + ", ".join(RESULTS)),
        Check("month_column_consistent", int(stats.get("wrong_month") or 0) == 0, int(stats.get("wrong_month") or 0), 0,
              f"month column = {month}"),
        Check("not_empty", rows > 0, rows, None),
    ]
    return checks


# ---------------------------------------------------------------------------- Spark side

def manifest_counts(spark, root: str) -> dict[str, int]:
    from pyspark.sql import functions as F
    df = (spark.read.option("multiLine", "true").json(f"{root}/_state/raw_games/*.json")
          .select("month", F.col("games").cast("long").alias("games")))
    return {r["month"]: r["games"] for r in df.collect()}


def read_staging(spark, root: str, month: str):
    # The files carry their own `month` column; recursiveFileLookup skips Hive-style
    # partition discovery, which would add a second one from the directory name.
    return spark.read.option("recursiveFileLookup", "true").parquet(f"{root}/raw/games/month={month}")


def table_exists(spark, name: str = TABLE) -> bool:
    return spark.catalog.tableExists(name)


def ensure_tables(spark, sample_df) -> None:
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.raw")
    if not table_exists(spark):
        ddl = sample_df._jdf.schema().toDDL()
        spark.sql(f"""
            CREATE TABLE {TABLE} ({ddl})
            USING iceberg
            PARTITIONED BY (month)
            TBLPROPERTIES (
              'format-version' = '2',
              'write.parquet.compression-codec' = 'zstd',
              'write.distribution-mode' = 'hash',
              'write.target-file-size-bytes' = '268435456',
              'comment' = 'Every Lichess standard game, one row per game: PGN header tags as strings, movetext, lineage. Partitioned by month (YYYY-MM).'
            )""")
    wap.ensure_main(spark, TABLE, sample_df.schema)
    wap.ensure_dq_table(spark)


def branch_stats(spark, branch: str, month: str) -> dict:
    allowed = ", ".join(f"'{r}'" for r in RESULTS)
    row = spark.sql(f"""
        SELECT count(*)                                                   AS rows,
               count_if(Site IS NULL OR Site = '')                         AS missing_site,
               count_if(Result IS NULL OR Result NOT IN ({allowed}))       AS bad_result,
               count_if(month <> '{month}')                                AS wrong_month
        FROM {TABLE} VERSION AS OF '{branch}'
        WHERE month = '{month}'""").first()
    return row.asDict()


def publish_month(spark, root: str, month: str, expected_games: int | None, run_id: str | None = None) -> list[Check]:
    """Write → audit → publish one month. Returns the checks; raises nothing on a failed audit."""
    staged = read_staging(spark, root, month)
    ensure_tables(spark, staged)
    audit = lambda branch: evaluate(month, branch_stats(spark, branch, month), expected_games)  # noqa: E731
    return wap.publish(spark, TABLE, month, staged, audit, run_id or uuid.uuid4().hex)


def published_months(spark) -> set[str]:
    if not table_exists(spark) or not wap.has_main(spark, TABLE):
        return set()
    return {r[0] for r in spark.sql(f"SELECT DISTINCT partition.month FROM {TABLE}.partitions").collect()}
