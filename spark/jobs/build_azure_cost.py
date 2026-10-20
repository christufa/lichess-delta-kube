"""Load Azure costs of the project's resource groups into lake.ops.azure_cost (#31).

  spark-job job=build_azure_cost args='[]'                            # previous + current month (daily)
  spark-job job=build_azure_cost args='["--months", "2026-09,2026-10"]'

Signs in as the jobs identity (Cost Management Reader on COST_RESOURCE_GROUPS only).
Exits 1 if a month was rejected.
"""
from __future__ import annotations

import argparse
import sys
import uuid

from lichess_spark import cost, get_spark, wap


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--months", help="comma-separated YYYY-MM (default: previous and current month)")
    args = p.parse_args(argv)
    months = [m.strip() for m in args.months.split(",") if m.strip()] if args.months else cost.recent_months()

    spark = get_spark("build-azure-cost")
    run_id = uuid.uuid4().hex
    failed = []
    for month in months:
        checks = cost.build_month(spark, month, run_id)
        ok = wap.gate(checks)
        failed += [] if ok else [month]
        print(f"{month}: {'PUBLISHED' if ok else 'REJECTED'}  " + ", ".join(f"{c.name}={wap.show(c)}" for c in checks))
    total = spark.sql(f"SELECT month, round(sum(cost), 2), first(currency) FROM {cost.TABLE} GROUP BY 1 ORDER BY 1").collect()
    for r in total:
        print(f"  {r[0]}: {r[1]} {r[2]}")
    spark.stop()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
