"""Build lake.curated.player_games from lake.curated.games, a month at a time (write-audit-publish).

  spark-job job=build_player_games args='["--months", "2013-01,2013-02"]'
  spark-job job=build_player_games args='["--pending"]'   # games months not built yet or rebuilt since

Each month: two rows per game (one per player, nothing filtered), written to a branch and
audited; main is fast-forwarded only if every blocking check passes. Exits 1 if any month was rejected.
"""
import sys

from lichess_spark import player_games, wap

if __name__ == "__main__":
    sys.exit(wap.cli("build-player-games", player_games.pending_months, player_games.build_month))
