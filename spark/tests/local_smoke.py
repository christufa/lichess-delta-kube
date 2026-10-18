"""End-to-end check of the smoke job in Spark local mode (run inside the image in CI):

  spark-submit --master 'local[2]' /opt/lichess/tests/local_smoke.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, "/opt/lichess/jobs")
import smoke_raw_counts  # noqa: E402

root = tempfile.mkdtemp()
os.environ["LICHESS_DEST"] = "file://" + root
from lichess_spark import get_spark  # noqa: E402

spark = get_spark("local-smoke")
for month, n in [("2013-01", 120), ("2013-02", 80)]:
    spark.range(n).selectExpr(f"'{month}' AS month", "CAST(id AS STRING) AS Site") \
        .write.parquet(f"{root}/raw/games/month={month}")
    os.makedirs(f"{root}/_state/raw_games", exist_ok=True)
    with open(f"{root}/_state/raw_games/{month}.json", "w") as fh:
        json.dump({"month": month, "games": n if month == "2013-01" else 81}, fh)  # 2013-02 is wrong on purpose

rows = {r["month"]: r for r in smoke_raw_counts.compare(spark, "file://" + root).collect()}
assert rows["2013-01"]["rows"] == 120 and rows["2013-01"]["ok"], rows
assert rows["2013-02"]["rows"] == 80 and not rows["2013-02"]["ok"], rows
print("local smoke ok:", {m: (r["rows"], r["expected"]) for m, r in rows.items()})
spark.stop()
