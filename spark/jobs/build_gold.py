"""Build the gold tables (lake.gold.*) from the curated tables, a month at a time (write-audit-publish).

  spark-job job=build_gold args='["--months", "2013-01,2013-02"]'
  spark-job job=build_gold args='["--pending"]'   # player_games months not built yet or rebuilt since

Each month and table: aggregate, write to a branch, check the totals reconcile with curated,
fast-forward main only if they do. Exits 1 if any table rejected a month.
"""
import sys

from lichess_spark import gold, wap

if __name__ == "__main__":
    sys.exit(wap.cli("build-gold", gold.pending_months, gold.build_month))
