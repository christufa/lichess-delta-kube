"""raw.games write-audit-publish end to end in Spark local mode, with a local Iceberg (Hadoop)
catalog standing in for Lakekeeper. Run in CI inside the image:

  spark-submit --master 'local[2]' /opt/lichess/tests/local_raw.py
"""
import json
import os
import tempfile

root = "file://" + tempfile.mkdtemp()
wh = "file://" + tempfile.mkdtemp()
os.environ["LICHESS_DEST"] = root
os.environ.pop("LICHESS_CATALOG_URI", None)

from lichess_spark import get_spark  # noqa: E402
from lichess_spark import raw  # noqa: E402

spark = get_spark("local-raw", extra={
    "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
    "spark.sql.catalog.lake": "org.apache.iceberg.spark.SparkCatalog",
    "spark.sql.catalog.lake.type": "hadoop",
    "spark.sql.catalog.lake.warehouse": wh,
})

def land(month, n, bad_result=0):
    rows = [(f"https://lichess.org/{month}{i}", "1-0" if i >= bad_result else "??", month, f"{month}.pgn.zst")
            for i in range(n)]
    (spark.createDataFrame(rows, "Site string, Result string, month string, _source_file string")
     .coalesce(1).write.mode("overwrite").parquet(f"{root}/raw/games/month={month}"))
    os.makedirs(f"{root[7:]}/_state/raw_games", exist_ok=True)
    with open(f"{root[7:]}/_state/raw_games/{month}.json", "w") as fh:
        json.dump({"month": month, "games": n}, fh)

land("2013-01", 120)               # good
land("2013-02", 80, bad_result=5)  # 5 rows with an invalid Result → must be rejected
expected = raw.manifest_counts(spark, root)

c1 = raw.publish_month(spark, root, "2013-01", expected["2013-01"])
assert all(c.passed for c in c1), c1
c2 = raw.publish_month(spark, root, "2013-02", expected["2013-02"])
assert not all(c.passed for c in c2), c2

assert raw.published_months(spark) == {"2013-01"}, raw.published_months(spark)
assert spark.table(raw.TABLE).count() == 120
assert spark.sql(f"SELECT count(*) FROM {raw.TABLE}.refs WHERE name LIKE 'audit_%'").first()[0] == 0  # branches gone

# Re-publishing a month replaces it (idempotent), it doesn't append.
c1b = raw.publish_month(spark, root, "2013-01", expected["2013-01"])
assert all(c.passed for c in c1b) and spark.table(raw.TABLE).count() == 120

dq = spark.table(raw.DQ_TABLE)
assert dq.where("NOT passed").count() >= 1 and dq.where("partition = 'month=2013-02' AND check_name = 'result_valid'").first()["observed"] == 5
print("local raw ok:", raw.published_months(spark), spark.table(raw.TABLE).count(), "rows;",
      dq.count(), "checks recorded")
spark.stop()
