"""Compact an Iceberg table's data files with Iceberg's rewrite_data_files procedure.

  spark-job job=rewrite_data_files args='["--table", "curated.player_games"]'
  spark-job job=rewrite_data_files args='["--table", "curated.player_games", "--where", "month = \\"2026-09\\""]'

Rewrites with the table's own sort order (strategy sort) into files of the table's
write.target-file-size-bytes. Before and after, prints file counts and sizes per month.

Heads-up: a rewrite changes the partitions' last-updated snapshot, so tables built from this
one see those months as changed and rebuild them on their next --pending run.
"""
from __future__ import annotations

import argparse
import sys

from lichess_spark import get_spark
from lichess_spark.wap import CATALOG


def file_stats(spark, table: str) -> list:
    return spark.sql(f"""
        SELECT partition.month AS month, count(*) AS files,
               round(avg(file_size_in_bytes) / 1e6) AS avg_mb, round(sum(file_size_in_bytes) / 1e9, 2) AS gb
        FROM {CATALOG}.{table}.files GROUP BY 1 ORDER BY 1""").collect()


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--table", required=True, help="namespace.table inside the lake catalog, e.g. curated.player_games")
    p.add_argument("--where", help="only rewrite matching rows, e.g. month = \"2026-09\"")
    p.add_argument("--min-input-files", default="2", help="rewrite a file group only if it has at least this many files")
    args = p.parse_args(argv)

    spark = get_spark("rewrite-data-files")
    before = {r["month"]: r for r in file_stats(spark, args.table)}
    where = f", where => '{args.where}'" if args.where else ""
    result = spark.sql(f"""
        CALL {CATALOG}.system.rewrite_data_files(
          table => '{args.table}', strategy => 'sort'{where},
          options => map('min-input-files', '{args.min_input_files}', 'partial-progress.enabled', 'true'))""").first()
    print("rewrite:", result.asDict())
    for r in file_stats(spark, args.table):
        b = before.get(r["month"])
        was = f"{b['files']} files, {b['avg_mb']} MB avg" if b else "new"
        print(f"{r['month']}: {r['files']} files, {r['avg_mb']} MB avg, {r['gb']} GB  (was {was})")
    spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
