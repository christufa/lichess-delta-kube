"""raw → curated.games → gold.position_moves (the opening explorer) end to end in Spark local mode,
with a local Iceberg (Hadoop) catalog standing in for Lakekeeper. Move text in the three PGN styles
Lichess has used (plain, with clocks, with clocks and evals). Checks that move orders reaching one
position are merged, rare pairs are left out, a repeated game counts once, and the month's
checks pass. Run in CI inside the image:

  spark-submit --master 'local[2]' /opt/lichess/tests/local_explorer.py
"""
import datetime as dt
import json
import os
import tempfile

root = "file://" + tempfile.mkdtemp()
wh = "file://" + tempfile.mkdtemp()
os.environ["LICHESS_DEST"] = root
os.environ.pop("LICHESS_CATALOG_URI", None)

from lichess_spark import get_spark  # noqa: E402
from lichess_spark import curated, explorer, raw, wap  # noqa: E402

import sys
import traceback

COLUMNS = ("Site string, Event string, White string, Black string, Result string, UTCDate string, UTCTime string, "
           "WhiteElo string, BlackElo string, WhiteRatingDiff string, BlackRatingDiff string, WhiteTitle string, "
           "BlackTitle string, ECO string, Opening string, TimeControl string, Termination string, movetext string, "
           "month string, _source_file string, _ingested_at timestamp, _run_id string")
MONTH = "2013-01"
NF3_FIRST = "1. Nf3 d5 2. d4 Nf6 3. c4 e6 1-0"                                          # plain (2013)
D4_FIRST = "1. d4 { [%clk 0:03:00] } d5 { [%clk 0:03:00] } 2. Nf3 { [%clk 0:02:58] } Nf6 { [%clk 0:02:59] } 1/2-1/2"
E4_RARE = "1. e4 { [%eval 0.2] [%clk 0:10:00] } 1... e5 { [%eval 0.3] [%clk 0:10:00] } 2. Qh5?! Nc6 0-1"


def _annotate(exc):
    """Make the failure visible in the GitHub UI (::error lines become annotations)."""
    msg = (str(exc) or type(exc).__name__).replace("\n", " ")[:900]
    print(f"::error title=local_explorer {type(exc).__name__}::{msg}")
    for line in traceback.format_exc().strip().splitlines()[-6:]:
        print(f"::error title=local_explorer traceback::{line.strip()[:300]}")


def game(i, movetext, result, white_elo="1500", black_elo="1700", **over):
    row = {
        "Site": f"https://lichess.org/a{i:07d}", "Event": "Rated Blitz game", "White": f"w{i}", "Black": f"b{i}",
        "Result": result, "UTCDate": "2013.01.05", "UTCTime": "12:34:56", "WhiteElo": white_elo, "BlackElo": black_elo,
        "WhiteRatingDiff": "+12", "BlackRatingDiff": "-12", "WhiteTitle": None, "BlackTitle": None,
        "ECO": "A00", "Opening": "Test", "TimeControl": "180+2", "Termination": "Normal", "movetext": movetext,
        "month": MONTH, "_source_file": f"{MONTH}.pgn.zst", "_ingested_at": dt.datetime(2026, 10, 1),
        "_run_id": f"ingest-{MONTH}-1",
    }
    return {**row, **over}


def run():
    spark = get_spark("local-explorer", extra={
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        "spark.sql.catalog.lake": "org.apache.iceberg.spark.SparkCatalog",
        "spark.sql.catalog.lake.type": "hadoop",
        "spark.sql.catalog.lake.warehouse": wh,
    })
    games = ([game(i, NF3_FIRST, "1-0") for i in range(6)]
             + [game(10 + i, D4_FIRST, "1/2-1/2") for i in range(6)]
             + [game(20 + i, E4_RARE, "0-1") for i in range(3)])
    games[0] = game(0, NF3_FIRST, "1-0", white_elo="2500", black_elo="2501")      # the top-rated example
    games[1] = game(1, NF3_FIRST, "1-0", white_elo="?")                          # unknown rating
    games.append(game(0, NF3_FIRST, "1-0", white_elo="2500", black_elo="2501",
                      _source_file=f"{MONTH}b.pgn.zst"))                         # the same game again

    names = [c.split()[0] for c in COLUMNS.split(", ")]
    rows = [tuple(g[n] for n in names) for g in games]
    spark.createDataFrame(rows, COLUMNS).coalesce(1).write.mode("overwrite").parquet(f"{root}/raw/games/month={MONTH}")
    os.makedirs(f"{root[7:]}/_state/raw_games", exist_ok=True)
    with open(f"{root[7:]}/_state/raw_games/{MONTH}.json", "w") as fh:
        json.dump({"month": MONTH, "games": len(games)}, fh)
    assert wap.gate(raw.publish_month(spark, root, MONTH, len(games)))
    assert wap.gate(curated.build_month(spark, MONTH))

    assert explorer.pending_months(spark) == [MONTH]
    checks = explorer.build_month(spark, MONTH)
    print("checks:", checks)
    assert wap.gate(checks), checks
    assert explorer.pending_months(spark) == []

    t = spark.table(explorer.TABLE).where(f"month = '{MONTH}'")
    by = lambda pos, move: t.where(f"position = '{pos}' AND move = '{move}'").groupBy().sum(  # noqa: E731
        "games", "white_wins", "draws", "black_wins", "rated_games").first()

    start = explorer.START
    assert by(start, "Nf3")[0] == 6 and by(start, "d4")[0] == 6          # the duplicate counts once
    assert t.where(f"position = '{start}' AND move = 'e4'").count() == 0   # 3 games < MIN_GAMES

    # 1. Nf3 d5 2. d4 and 1. d4 d5 2. Nf3 reach one position: Nf6 from it merges both.
    pos = explorer.replay("Nf3 d5 d4".split())
    assert pos == explorer.replay("d4 d5 Nf3".split())
    games_, white, draws, black, rated = by(pos, "Nf6")
    assert (games_, white, draws, black) == (12, 6, 6, 0), (games_, white, draws, black)
    assert rated == 11  # one game has an unknown rating

    ex = t.where(f"position = '{start}' AND move = 'Nf3'").orderBy("example_rating", ascending=False).first()
    assert ex["example_game_id"] == "a0000000" and ex["example_rating"] == 2500.5, ex
    bands = {r["rating_band"] for r in t.where(f"position = '{start}' AND move = 'Nf3'").collect()}
    assert bands == {1600, 2400, None}, bands
    # Deeper plies are kept while the pair reaches MIN_GAMES (6 games played 3. c4).
    assert by(explorer.replay("Nf3 d5 d4 Nf6".split()), "c4")[0] == 6
    spark.stop()
    print("local explorer: OK")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001
        _annotate(e)
        raise
    sys.exit(0)
