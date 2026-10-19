"""Build lake.curated.games from lake.raw.games, a month at a time (write-audit-publish).

  spark-job job=build_curated_games args='["--months", "2013-01,2013-02"]'
  spark-job job=build_curated_games args='["--pending"]'   # raw months not built yet or republished since

Each month: every raw row becomes one typed row (nothing filtered), written to a branch and
audited; main is fast-forwarded only if every blocking check passes. All checks, blocking or
counted, go to lake.ops.dq_results. Exits 1 if any month was rejected.
"""
import sys

from lichess_spark import curated, wap

if __name__ == "__main__":
    sys.exit(wap.cli("build-curated-games", curated.pending_months, curated.build_month))
