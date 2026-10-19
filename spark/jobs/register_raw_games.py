"""Publish landed months into the Iceberg table lake.raw.games (write-audit-publish).

  spark-job job=register_raw_games args='["--months", "2013-01,2013-02"]'
  spark-job job=register_raw_games args='["--pending"]'     # every month with a manifest not yet published

Each month: write to a branch, audit (count vs manifest, Site, Result, month), fast-forward main
only if every check passes. Results go to lake.ops.dq_results. Exits 1 if any month failed.
"""
from __future__ import annotations

import argparse
import sys
import uuid

from lichess_spark import dest_root, get_spark
from lichess_spark import raw


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--months", help="comma-separated YYYY-MM")
    g.add_argument("--pending", action="store_true", help="months with a manifest that aren't published yet")
    args = p.parse_args(argv)

    root = dest_root()
    spark = get_spark("register-raw-games")
    expected = raw.manifest_counts(spark, root)
    if args.pending:
        months = sorted(set(expected) - raw.published_months(spark))
    else:
        months = [m.strip() for m in args.months.split(",") if m.strip()]
    print(f"months to publish: {months or 'none'}")

    run_id = uuid.uuid4().hex
    failed = []
    for month in months:
        checks = raw.publish_month(spark, root, month, expected.get(month), run_id)
        ok = all(c.passed for c in checks)
        failed += [] if ok else [month]
        detail = ", ".join(f"{c.name}={'ok' if c.passed else f'FAIL({c.observed} vs {c.expected})'}" for c in checks)
        print(f"{month}: {'PUBLISHED' if ok else 'REJECTED (main unchanged)'}  {detail}")
    print(f"run {run_id}: {len(months) - len(failed)} published, {len(failed)} rejected {failed or ''}")
    spark.stop()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
