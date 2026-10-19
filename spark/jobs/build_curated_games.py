"""Build lake.curated.games from lake.raw.games, a month at a time (write-audit-publish).

  spark-job job=build_curated_games args='["--months", "2013-01,2013-02"]'
  spark-job job=build_curated_games args='["--pending"]'   # raw months not built yet or republished since

Each month: every raw row becomes one typed row (nothing filtered), written to a branch and
audited; main is fast-forwarded only if every blocking check passes. All checks, blocking or
counted, go to lake.ops.dq_results. Exits 1 if any month was rejected.
"""
from __future__ import annotations

import argparse
import sys
import uuid

from lichess_spark import curated, get_spark, wap


def show(c: wap.Check) -> str:
    if c.passed:
        return "ok"
    return str(c.observed) if not c.blocking else f"FAIL({c.observed} vs {c.expected})"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--months", help="comma-separated YYYY-MM")
    g.add_argument("--pending", action="store_true", help="raw months not built yet, or republished in raw since")
    args = p.parse_args(argv)

    spark = get_spark("build-curated-games")
    if args.pending:
        months = curated.pending_months(spark)
    else:
        months = [m.strip() for m in args.months.split(",") if m.strip()]
    print(f"months to build: {months or 'none'}")

    run_id = uuid.uuid4().hex
    failed = []
    for month in months:
        checks = curated.build_month(spark, month, run_id)
        ok = wap.gate(checks)
        failed += [] if ok else [month]
        detail = ", ".join(f"{c.name}={show(c)}" for c in checks)
        print(f"{month}: {'PUBLISHED' if ok else 'REJECTED (main unchanged)'}  {detail}")
    print(f"run {run_id}: {len(months) - len(failed)} published, {len(failed)} rejected {failed or ''}")
    spark.stop()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
