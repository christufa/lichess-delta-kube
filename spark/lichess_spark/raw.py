"""raw.games: landed Parquet → Iceberg, one month at a time, with write-audit-publish.

    staging  <root>/raw/games/month=YYYY-MM/*.parquet   written by lichess-ingest
    manifest <root>/_state/raw_games/YYYY-MM.json         {"month": …, "games": N, …}
    table    lake.raw.games                               partitioned by month

publish_month():
  1. write the month into a private branch of the table (replacing that month there)
  2. audit the branch: row count = manifest, Site present, Result valid, month column consistent
  3. pass → fast-forward main to the branch; fail → main untouched
  The branch is dropped either way; every check lands in lake.ops.dq_results.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

TABLE = "lake.raw.games"
TABLE_REF = "raw.games"  # inside the `lake` catalog, for procedures
DQ_TABLE = "lake.ops.dq_results"
RESULTS = ("1-0", "0-1", "1/2-1/2", "*")


@dataclass
class Check:
    name: str
    passed: bool
    observed: int | None
    expected: int | None
    detail: str = ""


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


def branch_name(month: str) -> str:
    return "audit_" + month.replace("-", "_")


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


def has_snapshots(spark) -> bool:
    return spark.sql(f"SELECT 1 FROM {TABLE}.snapshots LIMIT 1").count() > 0


def ensure_tables(spark, sample_df) -> None:
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.raw")
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.ops")
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
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {DQ_TABLE} (
          run_id STRING, checked_at TIMESTAMP, table_name STRING, partition STRING,
          check_name STRING, passed BOOLEAN, observed BIGINT, expected BIGINT, detail STRING)
        USING iceberg
        TBLPROPERTIES ('format-version' = '2',
                       'comment' = 'One row per data quality check per run (write-audit-publish gates).')""")


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


def record(spark, run_id: str, month: str, checks: list[Check]) -> None:
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rows = [(run_id, now, TABLE, f"month={month}", c.name, c.passed, c.observed, c.expected, c.detail) for c in checks]
    schema = ("run_id string, checked_at timestamp, table_name string, partition string, check_name string, "
              "passed boolean, observed bigint, expected bigint, detail string")
    spark.createDataFrame(rows, schema).writeTo(DQ_TABLE).append()


def publish_month(spark, root: str, month: str, expected_games: int | None, run_id: str | None = None) -> list[Check]:
    """Write → audit → publish one month. Returns the checks; raises nothing on a failed audit."""
    run_id = run_id or uuid.uuid4().hex
    staged = read_staging(spark, root, month)
    ensure_tables(spark, staged)
    branch = branch_name(month)
    spark.sql(f"ALTER TABLE {TABLE} DROP BRANCH IF EXISTS `{branch}`")  # leftovers from a crashed run
    if has_snapshots(spark):
        spark.sql(f"ALTER TABLE {TABLE} CREATE BRANCH `{branch}`")
    # On a brand-new table the write itself creates the branch.
    staged.writeTo(f"{TABLE}.branch_{branch}").overwritePartitions()

    checks = evaluate(month, branch_stats(spark, branch, month), expected_games)
    if all(c.passed for c in checks):
        if has_main(spark):
            spark.sql(f"CALL lake.system.fast_forward('{TABLE_REF}', 'main', '{branch}')")
        else:  # first publish: main doesn't exist yet, point it at the audited snapshot
            snap = spark.sql(f"SELECT snapshot_id FROM {TABLE}.refs WHERE name = '{branch}'").first()["snapshot_id"]
            spark.sql(f"CALL lake.system.set_current_snapshot('{TABLE_REF}', {snap})")
    spark.sql(f"ALTER TABLE {TABLE} DROP BRANCH IF EXISTS `{branch}`")
    record(spark, run_id, month, checks)
    return checks


def has_main(spark) -> bool:
    return spark.sql(f"SELECT 1 FROM {TABLE}.refs WHERE name = 'main'").count() > 0


def published_months(spark) -> set[str]:
    if not table_exists(spark) or not has_main(spark):
        return set()
    return {r[0] for r in spark.sql(f"SELECT DISTINCT partition.month FROM {TABLE}.partitions").collect()}
