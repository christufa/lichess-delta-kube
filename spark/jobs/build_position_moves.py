"""Build the opening explorer table (lake.gold.position_moves) a month at a time (write-audit-publish).

  spark-job job=build_position_moves args='["--months", "2026-09"]'
  spark-job job=build_position_moves args='["--pending"]'   # curated months not built yet or rebuilt since

Each month: the first 16 plies of every game, the (sequence, move) pairs played at least 5
times, merged by position, written to a branch, checked, fast-forwarded to main only if the
checks pass (lichess_spark.explorer). Exits 1 if any month was rejected.
"""
import sys

from lichess_spark import explorer, wap

if __name__ == "__main__":
    sys.exit(wap.cli("build-position-moves", explorer.pending_months, explorer.build_month))
