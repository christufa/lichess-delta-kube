"""curated.games end to end in Spark local mode, with a local Iceberg (Hadoop) catalog standing
in for Lakekeeper: raw months published first, then built into curated. Run in CI inside the image:

  spark-submit --master 'local[2]' /opt/lichess/tests/local_curated.py
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
from lichess_spark import curated, raw, wap  # noqa: E402

import sys
import traceback

COLUMNS = ("Site string, Event string, White string, Black string, Result string, UTCDate string, UTCTime string, "
           "WhiteElo string, BlackElo string, WhiteRatingDiff string, BlackRatingDiff string, WhiteTitle string, "
           "BlackTitle string, ECO string, Opening string, TimeControl string, Termination string, "
           "month string, _source_file string, _ingested_at timestamp")
TIME_CONTROLS = ["60+0", "180+2", "300+5", "900+10", "1800+30", "15+0", "-"]


def _annotate(exc):
    """Make the failure visible in the GitHub UI (::error lines become annotations)."""
    msg = (str(exc) or type(exc).__name__).replace("\n", " ")[:900]
    print(f"::error title=local_curated {type(exc).__name__}::{msg}")
    for line in traceback.format_exc().strip().splitlines()[-6:]:
        print(f"::error title=local_curated traceback::{line.strip()[:300]}")


def game(month, i, **over):
    y, m = month.split("-")
    row = {
        "Site": f"https://lichess.org/{m}{i:06d}", "Event": "Rated Blitz game", "White": f"w{i}", "Black": f"b{i}",
        "Result": "1-0", "UTCDate": f"{y}.{m}.05", "UTCTime": "12:34:56", "WhiteElo": "1500", "BlackElo": "1600",
        "WhiteRatingDiff": "+12", "BlackRatingDiff": "-12", "WhiteTitle": None, "BlackTitle": None,
        "ECO": "C20", "Opening": "King's Pawn Game", "TimeControl": TIME_CONTROLS[i % len(TIME_CONTROLS)],
        "Termination": "Normal", "month": month, "_source_file": f"{month}.pgn.zst",
        "_ingested_at": dt.datetime(2026, 10, 1),
    }
    return {**row, **over}


def run():
    spark = get_spark("local-curated", extra={
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        "spark.sql.catalog.lake": "org.apache.iceberg.spark.SparkCatalog",
        "spark.sql.catalog.lake.type": "hadoop",
        "spark.sql.catalog.lake.warehouse": wh,
    })
    names = [c.split()[0] for c in COLUMNS.split(", ")]

    def land_and_publish(month, games):
        rows = [tuple(g[n] for n in names) for g in games]
        (spark.createDataFrame(rows, COLUMNS).coalesce(1)
         .write.mode("overwrite").parquet(f"{root}/raw/games/month={month}"))
        os.makedirs(f"{root[7:]}/_state/raw_games", exist_ok=True)
        with open(f"{root[7:]}/_state/raw_games/{month}.json", "w") as fh:
            json.dump({"month": month, "games": len(games)}, fh)
        checks = raw.publish_month(spark, root, month, len(games))
        assert wap.gate(checks), checks

    # 2013-01: good, with the unusual things curated must keep (not filter).
    jan = [game("2013-01", i) for i in range(40)]
    jan[1] = game("2013-01", 1, Result="*", Termination="Abandoned")       # unfinished
    jan[2] = game("2013-01", 2, WhiteElo="3700", WhiteTitle="BOT")        # rating out of range, a bot
    jan[3] = game("2013-01", 3, WhiteElo="?", WhiteRatingDiff=None)       # unknown rating
    jan[4] = game("2013-01", 4, Termination="Something new")              # unknown termination
    jan[5] = game("2013-01", 5, Event="Rated Blitz tournament https://lichess.org/tournament/winter15")
    jan.append(game("2013-01", 0, _source_file="2013-01b.pgn.zst"))       # same game id again
    land_and_publish("2013-01", jan)
    # 2013-02: one unparseable time control → must be rejected (main untouched).
    feb = [game("2013-02", i) for i in range(10)]
    feb[3] = game("2013-02", 3, TimeControl="abc")
    land_and_publish("2013-02", feb)

    assert curated.pending_months(spark) == ["2013-01", "2013-02"], curated.pending_months(spark)
    c1 = {c.name: c for c in curated.build_month(spark, "2013-01")}
    assert wap.gate(list(c1.values())), c1
    assert c1["game_id_unique"].observed == 1 and c1["rating_in_range"].observed == 1, c1
    assert c1["result_decided"].observed == 1 and c1["termination_known"].observed == 1, c1
    c2 = {c.name: c for c in curated.build_month(spark, "2013-02")}
    assert not wap.gate(list(c2.values())) and c2["time_control_parseable"].observed == 1, c2

    games = spark.table(curated.TABLE)
    assert games.count() == len(jan), games.count()                       # nothing filtered, Feb not published
    assert {r[0] for r in games.select("month").distinct().collect()} == {"2013-01"}
    assert games.where("is_duplicate").count() == 1
    assert games.where("NOT is_duplicate").select("game_id").distinct().count() == len(jan) - 1
    assert spark.sql(f"SELECT count(*) FROM {curated.TABLE}.refs WHERE name LIKE 'audit_%'").first()[0] == 0

    by_id = {r["game_id"]: r for r in games.where("NOT is_duplicate").collect()}
    assert by_id["01000001"]["result"] is None and by_id["01000000"]["result"] == "white"
    assert by_id["01000002"]["white_elo"] == 3700 and by_id["01000002"]["white_is_bot"]
    assert by_id["01000003"]["white_elo"] is None and by_id["01000003"]["white_rating_diff"] is None
    assert by_id["01000000"]["white_rating_diff"] == 12 and by_id["01000000"]["black_rating_diff"] == -12
    assert by_id["01000005"]["tournament_id"] == "winter15"
    played = spark.sql(f"SELECT CAST(played_at AS STRING) FROM {curated.TABLE} WHERE game_id = '01000000' LIMIT 1").first()[0]
    assert played == "2013-01-05 12:34:56", played                        # UTC, session time zone is UTC
    # The Spark expression and the Python reference agree on every time control in the data.
    for r in games.select("time_control", "base_seconds", "increment_seconds", "speed").distinct().collect():
        assert curated.parse_time_control(r[0]) == (r[1], r[2], r[3]), r

    # Lineage: nothing pending but the rejected month; republishing raw makes the month pending again.
    assert curated.pending_months(spark) == ["2013-02"], curated.pending_months(spark)
    land_and_publish("2013-01", jan)
    assert curated.pending_months(spark) == ["2013-01", "2013-02"], curated.pending_months(spark)
    assert wap.gate(curated.build_month(spark, "2013-01"))
    assert spark.table(curated.TABLE).count() == len(jan)                 # rebuilt, not appended

    dq = spark.table(wap.DQ_TABLE).where(f"table_name = '{curated.TABLE}'")
    assert dq.where("NOT passed AND blocking").count() == 1                # Feb's time control
    assert dq.where("NOT passed AND NOT blocking").count() >= 4
    print("local curated ok:", spark.table(curated.TABLE).count(), "rows;", dq.count(), "curated checks recorded")
    spark.stop()


try:
    run()
except Exception as exc:  # noqa: BLE001
    _annotate(exc)
    sys.exit(1)
