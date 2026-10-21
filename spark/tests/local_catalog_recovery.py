"""Catalog recovery end to end in Spark local mode: a local Iceberg (Hadoop) catalog stands in for
Lakekeeper. The inventory is written and read back; then every table is found again from the
inventory and its files alone (newest metadata file, including commits made after the inventory)
and reads the same rows as the catalog. Registering in Lakekeeper is the live drill's part
(`catalog_recovery live-drill`). Run in CI inside the image:

  spark-submit --master 'local[2]' /opt/lichess/tests/local_catalog_recovery.py
"""
import os
import sys
import tempfile
import traceback

root = "file://" + tempfile.mkdtemp()
wh = tempfile.mkdtemp()
os.environ["LICHESS_DEST"] = root
os.environ.pop("LICHESS_CATALOG_URI", None)

from lichess_spark import catalog_recovery as cr  # noqa: E402
from lichess_spark import get_spark  # noqa: E402


def _annotate(exc):
    """Make the failure visible in the GitHub UI (::error lines become annotations)."""
    msg = (str(exc) or type(exc).__name__).replace("\n", " ")[:900]
    print(f"::error title=local_catalog_recovery {type(exc).__name__}::{msg}")
    for line in traceback.format_exc().strip().splitlines()[-6:]:
        print(f"::error title=local_catalog_recovery traceback::{line.strip()[:300]}")


def run():
    spark = get_spark("local-catalog-recovery", extra={
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        "spark.sql.catalog.lake": "org.apache.iceberg.spark.SparkCatalog",
        "spark.sql.catalog.lake.type": "hadoop",
        "spark.sql.catalog.lake.warehouse": "file://" + wh,
    })
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.gold")
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.ops")
    spark.sql("CREATE TABLE lake.gold.summary (month STRING, games BIGINT) USING iceberg "
              "PARTITIONED BY (month) COMMENT 'Games per month'")
    spark.sql("CREATE TABLE lake.ops.log (id BIGINT) USING iceberg")
    spark.sql("INSERT INTO lake.gold.summary VALUES ('2013-01', 121332), ('2013-02', 123961)")
    spark.sql("INSERT INTO lake.ops.log SELECT id FROM range(10)")

    entries = cr.snapshot(spark, root)
    assert [e.table for e in entries] == ["gold.summary", "ops.log"], entries
    assert entries[0].comment == "Games per month", entries[0]
    assert cr.load(spark, root) == entries  # read back from the lake

    # A commit after the inventory: restore must pick it up from the files.
    spark.sql("INSERT INTO lake.gold.summary VALUES ('2013-03', 158635)")

    files = {}
    for e in cr.load(spark, root):
        files[e.table] = file = cr.latest_file(spark, e)
        assert cr.read_files(spark, file).count() == cr.count(spark, e.table), e.table
        print(f"{e.table}: found again as {file.rsplit('/', 1)[-1]}")
    summary = cr.read_files(spark, files["gold.summary"])
    assert sorted(r.month for r in summary.collect()) == ["2013-01", "2013-02", "2013-03"]  # the later commit too
    assert cr.read_files(spark, entries[0].metadata).count() == 2  # the inventory's own file: before it
    spark.stop()
    print("local catalog recovery: OK")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:  # noqa: BLE001
        _annotate(e)
        raise
    sys.exit(0)
