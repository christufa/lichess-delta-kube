"""Smoke test for the Iceberg catalog: create a table through Lakekeeper, write, read back.

  spark-job job=catalog_smoke

Writes go to the table's location using the SAS token Lakekeeper vends for that table.
Leaves lake.ops.catalog_smoke with one row per run (harmless, and shows the history).
"""
from __future__ import annotations

import sys

from lichess_spark import get_spark


def main() -> int:
    spark = get_spark("catalog-smoke")
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.ops")
    spark.sql("""
        CREATE TABLE IF NOT EXISTS lake.ops.catalog_smoke (run_at TIMESTAMP, spark_version STRING)
        USING iceberg
    """)
    before = spark.table("lake.ops.catalog_smoke").count()
    spark.sql(f"INSERT INTO lake.ops.catalog_smoke VALUES (current_timestamp(), '{spark.version}')")
    after = spark.table("lake.ops.catalog_smoke").count()
    location = spark.sql("DESCRIBE TABLE EXTENDED lake.ops.catalog_smoke").where("col_name = 'Location'").first()
    snapshots = spark.sql("SELECT count(*) AS n FROM lake.ops.catalog_smoke.snapshots").first()["n"]
    print(f"catalog smoke: rows {before} -> {after}, snapshots {snapshots}, location {location['data_type'] if location else '?'}")
    return 0 if after == before + 1 else 1


if __name__ == "__main__":
    sys.exit(main())
