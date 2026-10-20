"""Write-audit-publish for Iceberg tables, one partition at a time (used by raw and curated).

publish():
  1. write into a private branch of the table (from main's current snapshot)
  2. audit the branch → checks
  3. every blocking check passed → fast-forward main to the branch; otherwise main is untouched
  The branch is dropped either way; every check lands in lake.ops.dq_results.

Non-blocking checks are recorded (passed or not) but never hold a partition back: they count
what's unusual in the data without filtering it.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable

CATALOG = "lake"
DQ_TABLE = "lake.ops.dq_results"


@dataclass
class Check:
    name: str
    passed: bool
    observed: int | None
    expected: int | None
    detail: str = ""
    blocking: bool = True


def gate(checks: list[Check]) -> bool:
    """True when the partition may be published: every blocking check passed."""
    return all(c.passed for c in checks if c.blocking)


def ref(table: str) -> str:
    """lake.raw.games → raw.games (the name procedures take inside the catalog)."""
    return table.removeprefix(CATALOG + ".")


def branch_name(month: str) -> str:
    return "audit_" + month.replace("-", "_")


# ---------------------------------------------------------------------------- Spark side

def has_main(spark, table: str) -> bool:
    return spark.sql(f"SELECT 1 FROM {table}.refs WHERE name = 'main'").count() > 0


def table_exists(spark, table: str) -> bool:
    return spark.catalog.tableExists(table)


# Lineage: every row carries the run that wrote it (_run_id: the ingest run for raw, the build
# run for curated and gold). A derived table records, per month, the source's run id it was
# built from (_source_run_id). A rebuild of the source month changes its run id; compaction and
# other maintenance rewrite files but keep the rows, so they don't make anything look stale.
SOURCE_RUN_ID = "_source_run_id"
LEGACY_LINEAGE = ("_raw_snapshot_id", "_games_snapshot_id", "_player_games_snapshot_id")


def source_versions(spark, table: str) -> dict[str, str]:
    """month → the run id of that month's rows on main (one run writes a whole month)."""
    if not table_exists(spark, table) or not has_main(spark, table):
        return {}
    rows = spark.sql(f"SELECT month, max(_run_id) FROM {table} GROUP BY month").collect()
    return {r[0]: r[1] for r in rows}


def built_from(spark, table: str) -> dict[str, str]:
    """month → the source run id each published month of `table` was built from."""
    if not table_exists(spark, table) or not has_main(spark, table):
        return {}
    if SOURCE_RUN_ID not in spark.table(table).columns:  # built before run-id lineage: rebuild
        return {}
    rows = spark.sql(f"SELECT month, max({SOURCE_RUN_ID}) FROM {table} GROUP BY month").collect()
    return {r[0]: r[1] for r in rows}


def pending(spark, source: str, target: str) -> list[str]:
    """Source months not in target yet, or rebuilt in source since target was built from them."""
    have, built = source_versions(spark, source), built_from(spark, target)
    return sorted(m for m, run in have.items() if run is not None and built.get(m) != run)


def pinned_month(spark, source: str, month: str):
    """(source run id, rows of `month`), both from main's current snapshot, so they agree."""
    from pyspark.sql import functions as F

    snapshot = spark.sql(f"SELECT snapshot_id FROM {source}.refs WHERE name = 'main'").first()[0]
    rows = spark.sql(f"SELECT * FROM {source} VERSION AS OF {snapshot} WHERE month = '{month}'")
    return rows.agg(F.max("_run_id")).first()[0], rows


def migrate_lineage(spark, table: str) -> None:
    """Tables from before run-id lineage: add _source_run_id, drop the snapshot-id columns."""
    if not table_exists(spark, table):
        return
    columns = spark.table(table).columns
    if SOURCE_RUN_ID not in columns:
        spark.sql(f"ALTER TABLE {table} ADD COLUMN {SOURCE_RUN_ID} STRING")
    for legacy in LEGACY_LINEAGE:
        if legacy in columns:
            spark.sql(f"ALTER TABLE {table} DROP COLUMN {legacy}")


def ensure_main(spark, table: str, schema) -> None:
    """An empty first snapshot on main, so every partition (the first too) can branch from it."""
    if not has_main(spark, table):
        spark.createDataFrame([], schema).writeTo(table).append()


def ensure_dq_table(spark) -> None:
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {CATALOG}.ops")
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {DQ_TABLE} (
          run_id STRING, checked_at TIMESTAMP, table_name STRING, partition STRING,
          check_name STRING, passed BOOLEAN, observed BIGINT, expected BIGINT, detail STRING,
          blocking BOOLEAN)
        USING iceberg
        TBLPROPERTIES ('format-version' = '2',
                       'comment' = 'One row per data quality check per run (write-audit-publish gates).')""")
    # Tables created before checks could be non-blocking: add the column (null = blocking).
    if "blocking" not in spark.table(DQ_TABLE).columns:
        spark.sql(f"ALTER TABLE {DQ_TABLE} ADD COLUMN blocking BOOLEAN")


def record(spark, run_id: str, table: str, partition: str, checks: list[Check]) -> None:
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rows = [(run_id, now, table, partition, c.name, c.passed, c.observed, c.expected, c.detail, c.blocking)
            for c in checks]
    schema = ("run_id string, checked_at timestamp, table_name string, partition string, check_name string, "
              "passed boolean, observed bigint, expected bigint, detail string, blocking boolean")
    spark.createDataFrame(rows, schema).writeTo(DQ_TABLE).append()


def cli(app: str, pending: Callable, build: Callable, argv=None) -> int:
    """The command line every build job shares: --months a,b | --pending; exits 1 if any month was rejected."""
    import argparse
    import uuid

    from .session import get_spark

    p = argparse.ArgumentParser(prog=app)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--months", help="comma-separated YYYY-MM")
    g.add_argument("--pending", action="store_true", help="source months not built yet, or changed since")
    args = p.parse_args(argv)

    spark = get_spark(app)
    months = pending(spark) if args.pending else [m.strip() for m in args.months.split(",") if m.strip()]
    print(f"months to build: {months or 'none'}")
    run_id = uuid.uuid4().hex
    failed = []
    for month in months:
        checks = build(spark, month, run_id)
        ok = gate(checks)
        failed += [] if ok else [month]
        print(f"{month}: {'PUBLISHED' if ok else 'REJECTED (failed checks kept it off main)'}  " + ", ".join(f"{c.name}={show(c)}" for c in checks))
    print(f"run {run_id}: {len(months) - len(failed)} published, {len(failed)} rejected {failed or ''}")
    spark.stop()
    return 1 if failed else 0


def show(c: Check) -> str:
    if c.passed:
        return "ok"
    return str(c.observed) if not c.blocking else f"FAIL({c.observed} vs {c.expected})"


def publish(spark, table: str, month: str, df, audit: Callable[[str], list[Check]], run_id: str) -> list[Check]:
    """Write df (one month) → audit → publish. Returns the checks; raises nothing on a failed audit."""
    from pyspark.sql import functions as F

    branch = branch_name(month)
    spark.sql(f"ALTER TABLE {table} DROP BRANCH IF EXISTS `{branch}`")  # leftovers from a crashed run
    spark.sql(f"ALTER TABLE {table} CREATE BRANCH `{branch}`")         # from main's current snapshot
    # Replace the whole month, not just the partitions df touches: with a bucket in the
    # partition spec, a bucket missing from df would otherwise keep its old rows.
    df.writeTo(f"{table}.branch_{branch}").overwrite(F.col("month") == F.lit(month))

    checks = audit(branch)
    if gate(checks):
        spark.sql(f"CALL {CATALOG}.system.fast_forward('{ref(table)}', 'main', '{branch}')")
    spark.sql(f"ALTER TABLE {table} DROP BRANCH IF EXISTS `{branch}`")
    record(spark, run_id, table, f"month={month}", checks)
    return checks
