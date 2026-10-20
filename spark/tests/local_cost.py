"""ops.azure_cost write-audit-publish in Spark local mode, with a local Iceberg (Hadoop) catalog and
rows standing in for the Cost Management API. Run in CI inside the image:

  spark-submit --master 'local[2]' /opt/lichess/tests/local_cost.py
"""
import datetime as dt
import os
import tempfile

root = "file://" + tempfile.mkdtemp()
os.environ["LICHESS_DEST"] = root
os.environ.pop("LICHESS_CATALOG_URI", None)

from lichess_spark import cost, get_spark, wap  # noqa: E402

import sys
import traceback


def _annotate(exc):
    msg = (str(exc) or type(exc).__name__).replace("\n", " ")[:900]
    print(f"::error title=local_cost {type(exc).__name__}::{msg}")
    for line in traceback.format_exc().strip().splitlines()[-6:]:
        print(f"::error title=local_cost traceback::{line.strip()[:300]}")


def run():
    spark = get_spark("local-cost", extra={
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        "spark.sql.catalog.lake": "org.apache.iceberg.spark.SparkCatalog",
        "spark.sql.catalog.lake.type": "hadoop",
        "spark.sql.catalog.lake.warehouse": "file://" + tempfile.mkdtemp(),
    })
    day = lambda d: dt.date(2026, 10, d)  # noqa: E731
    rows = [("2026-10", day(1), "rg-a", "Virtual Machines", "vmss", 2.5, "USD"),
            ("2026-10", day(2), "rg-b", "Storage", "storageaccounts", 0.75, "USD"),
            ("2026-10", day(2), "rg-b", "Storage", "storageaccounts", -0.25, "USD")]
    c1 = cost.build_month(spark, "2026-10", rows=rows)
    assert wap.gate(c1), c1
    assert {c.name: c.observed for c in c1}["no_negative_costs"] == 1
    assert spark.sql(f"SELECT round(sum(cost), 2) FROM {cost.TABLE}").first()[0] == 3.0

    # The next day's run replaces the month (costs settle), it doesn't append.
    c2 = cost.build_month(spark, "2026-10", rows=rows[:2])
    assert wap.gate(c2) and spark.table(cost.TABLE).count() == 2

    # A row outside the month is rejected; main keeps the previous build.
    bad = rows[:1] + [("2026-10", dt.date(2026, 9, 30), "rg-a", "VM", "vmss", 1.0, "USD")]
    assert not wap.gate(cost.build_month(spark, "2026-10", rows=bad))
    assert spark.table(cost.TABLE).count() == 2

    # The 1st of a month with no costs yet still publishes (empty).
    assert wap.gate(cost.build_month(spark, "2026-11", rows=[]))
    print("local cost ok:", spark.table(cost.TABLE).count(), "rows")
    spark.stop()


try:
    run()
except Exception as exc:  # noqa: BLE001
    _annotate(exc)
    sys.exit(1)
