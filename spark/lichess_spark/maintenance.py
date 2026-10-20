"""Iceberg table maintenance for every table in the lake (#63).

Per table, in order:
  1. rewrite_data_files   compact small files: strategy sort where the table has a sort order
                          (curated), binpack otherwise; only file groups with enough small files
  2. rewrite_manifests    merge small manifests so planning stays fast
  3. expire_snapshots     drop snapshots older than N days (keeping the last few); snapshots
                          that a branch or tag points at are never expired. Frees the data
                          files only those snapshots used.
  4. remove_orphan_files  delete files under the table location that no snapshot references
                          (failed writes), older than N days so in-flight writes are safe

Settings come from table properties, falling back to the job's defaults:
  lichess.maintenance.rewrite          sort | binpack | none   (default: sort if sorted, else binpack)
  lichess.maintenance.min-input-files  rewrite a file group only with at least this many files
  lichess.maintenance.expire-days      snapshot age to expire
  lichess.maintenance.retain-last      snapshots always kept
  lichess.maintenance.orphan-days      orphan age to delete

None of this changes rows, so it never makes a downstream table look stale: lineage is by
run id (lichess_spark.wap), not by snapshot.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .wap import CATALOG

NAMESPACES = ("raw", "curated", "gold", "ops")
PREFIX = "lichess.maintenance."
STRATEGIES = ("sort", "binpack", "none")


@dataclass(frozen=True)
class Settings:
    rewrite: str
    min_input_files: int
    expire_days: int
    retain_last: int
    orphan_days: int


DEFAULTS = Settings(rewrite="auto", min_input_files=5, expire_days=7, retain_last=3, orphan_days=3)


def settings(props: dict[str, str], defaults: Settings = DEFAULTS) -> Settings:
    """Resolve one table's settings from its properties (pure, unit-tested)."""
    get = lambda key, default: props.get(PREFIX + key, default)  # noqa: E731
    rewrite = get("rewrite", defaults.rewrite)
    if rewrite == "auto":
        rewrite = "sort" if props.get("sort-order") else "binpack"
    if rewrite not in STRATEGIES:
        raise ValueError(f"{PREFIX}rewrite must be one of {', '.join(STRATEGIES)}, not {rewrite!r}")
    return Settings(
        rewrite=rewrite,
        min_input_files=int(get("min-input-files", defaults.min_input_files)),
        expire_days=int(get("expire-days", defaults.expire_days)),
        retain_last=int(get("retain-last", defaults.retain_last)),
        orphan_days=int(get("orphan-days", defaults.orphan_days)),
    )


def cutoff(days: int, now: dt.datetime | None = None) -> str:
    """A TIMESTAMP literal `days` before now (UTC), for older_than."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return (now - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------- Spark side

def tables(spark, namespaces=NAMESPACES) -> list[str]:
    """Every table in the given namespaces, as namespace.table (the form procedures take)."""
    found = []
    existing = {r[0] for r in spark.sql(f"SHOW NAMESPACES IN {CATALOG}").collect()}
    for ns in namespaces:
        if ns in existing:
            found += [f"{ns}.{r['tableName']}" for r in spark.sql(f"SHOW TABLES IN {CATALOG}.{ns}").collect()]
    return sorted(found)


def stats(spark, table: str) -> dict:
    full = f"{CATALOG}.{table}"
    files = spark.sql(f"SELECT count(*) AS n, coalesce(sum(file_size_in_bytes), 0) AS bytes FROM {full}.files").first()
    snapshots = spark.sql(f"SELECT count(*) FROM {full}.snapshots").first()[0]
    manifests = spark.sql(f"SELECT count(*) FROM {full}.manifests").first()[0]
    return {"files": files["n"], "gb": round(files["bytes"] / 1e9, 2), "snapshots": snapshots, "manifests": manifests}


def maintain(spark, table: str, defaults: Settings = DEFAULTS, orphans_dry_run: bool = False,
             now: dt.datetime | None = None) -> dict:
    """Run the four steps on one table; returns before/after stats and what each step did."""
    full = f"{CATALOG}.{table}"
    props = {r[0]: r[1] for r in spark.sql(f"SHOW TBLPROPERTIES {full}").collect()}
    s = settings(props, defaults)
    report = {"table": table, "settings": s, "before": stats(spark, table)}

    if s.rewrite != "none":
        row = spark.sql(f"""
            CALL {CATALOG}.system.rewrite_data_files(
              table => '{table}', strategy => '{s.rewrite}',
              options => map('min-input-files', '{s.min_input_files}', 'partial-progress.enabled', 'true'))""").first()
        report["rewritten_files"], report["added_files"] = row["rewritten_data_files_count"], row["added_data_files_count"]
    spark.sql(f"CALL {CATALOG}.system.rewrite_manifests(table => '{table}')")
    row = spark.sql(f"""
        CALL {CATALOG}.system.expire_snapshots(
          table => '{table}', older_than => TIMESTAMP '{cutoff(s.expire_days, now)}', retain_last => {s.retain_last})""").first()
    report["expired_data_files"] = row["deleted_data_files_count"]
    orphans = spark.sql(f"""
        CALL {CATALOG}.system.remove_orphan_files(
          table => '{table}', older_than => TIMESTAMP '{cutoff(s.orphan_days, now)}',
          dry_run => {'true' if orphans_dry_run else 'false'})""").collect()
    report["orphans"] = [r[0] for r in orphans]
    report["after"] = stats(spark, table)
    return report
