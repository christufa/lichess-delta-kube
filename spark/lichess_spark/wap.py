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


def publish(spark, table: str, month: str, df, audit: Callable[[str], list[Check]], run_id: str) -> list[Check]:
    """Write df (one month) → audit → publish. Returns the checks; raises nothing on a failed audit."""
    branch = branch_name(month)
    spark.sql(f"ALTER TABLE {table} DROP BRANCH IF EXISTS `{branch}`")  # leftovers from a crashed run
    spark.sql(f"ALTER TABLE {table} CREATE BRANCH `{branch}`")         # from main's current snapshot
    df.writeTo(f"{table}.branch_{branch}").overwritePartitions()

    checks = audit(branch)
    if gate(checks):
        spark.sql(f"CALL {CATALOG}.system.fast_forward('{ref(table)}', 'main', '{branch}')")
    spark.sql(f"ALTER TABLE {table} DROP BRANCH IF EXISTS `{branch}`")
    record(spark, run_id, table, f"month={month}", checks)
    return checks
