"""Table maintenance end to end in Spark local mode, with a local Iceberg (Hadoop) catalog standing
in for Lakekeeper: small files get compacted, old snapshots expire (and their files go), an
orphan file is removed, rows never change, and a second run has nothing left to do. Run in CI
inside the image:

  spark-submit --master 'local[2]' /opt/lichess/tests/local_maintenance.py
"""
import datetime as dt
import glob
import os
import tempfile
import time

root = "file://" + tempfile.mkdtemp()
wh = tempfile.mkdtemp()
os.environ["LICHESS_DEST"] = root
os.environ.pop("LICHESS_CATALOG_URI", None)

from lichess_spark import get_spark  # noqa: E402
from lichess_spark import maintenance  # noqa: E402

import sys
import traceback


def _annotate(exc):
    """Make the failure visible in the GitHub UI (::error lines become annotations)."""
    msg = (str(exc) or type(exc).__name__).replace("\n", " ")[:900]
    print(f"::error title=local_maintenance {type(exc).__name__}::{msg}")
    for line in traceback.format_exc().strip().splitlines()[-6:]:
        print(f"::error title=local_maintenance traceback::{line.strip()[:300]}")


def run():
    spark = get_spark("local-maintenance", extra={
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        "spark.sql.catalog.lake": "org.apache.iceberg.spark.SparkCatalog",
        "spark.sql.catalog.lake.type": "hadoop",
        "spark.sql.catalog.lake.warehouse": "file://" + wh,
    })
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.curated")
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.ops")
    spark.sql("CREATE TABLE lake.curated.t (id BIGINT, month STRING, _run_id STRING) USING iceberg PARTITIONED BY (month)")
    spark.sql("ALTER TABLE lake.curated.t WRITE ORDERED BY id")
    spark.sql("CREATE TABLE lake.ops.log (id BIGINT, note STRING) USING iceberg")
    for i in range(8):  # eight small appends → eight files and snapshots per table
        spark.sql(f"INSERT INTO lake.curated.t SELECT id, '2013-01', 'run-1' FROM range({i * 100}, {(i + 1) * 100})")
        spark.sql(f"INSERT INTO lake.ops.log SELECT id, 'x' FROM range({i * 10}, {(i + 1) * 10})")
    spark.sql("DELETE FROM lake.curated.t WHERE id < 50")  # leaves files only old snapshots need
    rows = spark.table("lake.curated.t").count()

    # A failed write's leftover, four days old.
    orphan = f"{wh}/curated/t/data/month=2013-01/orphan.parquet"
    with open(orphan, "wb") as fh:
        fh.write(b"not referenced by any snapshot")
    old = time.time() - 4 * 86400
    os.utime(orphan, (old, old))

    assert maintenance.tables(spark) == ["curated.t", "ops.log"], maintenance.tables(spark)
    defaults = maintenance.DEFAULTS.__class__("auto", 2, 0, 1, 3)  # expire everything but the last snapshot
    # Cutoffs are whole seconds: with "now" they'd miss snapshots committed in the same second
    # (the compaction's own), so run as if a minute later.
    later = lambda: dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)  # noqa: E731
    r = maintenance.maintain(spark, "curated.t", defaults, now=later())
    print("curated.t:", r)
    assert r["settings"].rewrite == "sort"
    assert r["after"]["files"] < r["before"]["files"], r
    assert r["after"]["snapshots"] == 1 and r["expired_data_files"] > 0, r
    assert any(p.endswith("orphan.parquet") for p in r["orphans"]) and not os.path.exists(orphan), r["orphans"]
    assert spark.table("lake.curated.t").count() == rows
    assert spark.sql("SELECT DISTINCT _run_id FROM lake.curated.t").collect()[0][0] == "run-1"  # lineage kept

    r2 = maintenance.maintain(spark, "curated.t", defaults, now=later())
    assert r2.get("rewritten_files", 0) == 0 and r2["after"]["files"] == r["after"]["files"], r2  # bounded

    log = maintenance.maintain(spark, "ops.log", defaults, now=later())
    assert log["settings"].rewrite == "binpack" and log["after"]["files"] < log["before"]["files"], log
    assert spark.table("lake.ops.log").count() == 80

    left = glob.glob(f"{wh}/curated/t/data/**/*.parquet", recursive=True)
    print("local maintenance ok:", len(left), "data files left in curated.t;", rows, "rows")
    spark.stop()


try:
    run()
except Exception as exc:  # noqa: BLE001
    _annotate(exc)
    sys.exit(1)
