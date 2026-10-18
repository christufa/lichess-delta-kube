"""Smoke test: read raw/games from the lake and check row counts against the month manifests.

  spark-submit smoke_raw_counts.py [--dest abfss://…/bootstrap]

Exits non-zero if any month with a manifest has a different number of rows in its files.
"""
from __future__ import annotations

import argparse
import os
import sys

from lichess_spark import dest_root, get_spark


def compare(spark, root: str):
    from pyspark.sql import functions as F

    # recursiveFileLookup: the files already contain a `month` column, so skip Hive-style
    # partition discovery (it would add a second `month` from the directory names).
    games = (spark.read.option("recursiveFileLookup", "true").parquet(f"{root}/raw/games")
             .groupBy("month").agg(F.count(F.lit(1)).alias("rows")))
    manifests = (spark.read.option("multiLine", "true").json(f"{root}/_state/raw_games/*.json")
                 .select("month", F.col("games").alias("expected")))
    return (manifests.join(games, "month", "full_outer")
            .withColumn("ok", F.col("rows") == F.col("expected"))
            .orderBy("month"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dest", default=None, help="defaults to $LICHESS_DEST")
    args = p.parse_args(argv)
    if args.dest:
        os.environ["LICHESS_DEST"] = args.dest
    root = dest_root()
    spark = get_spark("lichess-smoke-raw-counts")
    result = compare(spark, root).collect()
    bad = [r for r in result if r["expected"] is not None and not r["ok"]]
    for r in result:
        print(f"{r['month']}  rows={r['rows']}  expected={r['expected']}  {'ok' if r['ok'] else 'MISMATCH'}")
    total = sum(r["rows"] or 0 for r in result)
    print(f"{len(result)} months, {total:,} rows, {len(bad)} mismatches")
    spark.stop()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
