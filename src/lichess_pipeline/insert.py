"""Load original HF Parquet with Spark and verify the committed Delta partition."""
from .common import partition_filter
from .observability import event, operation


def time_as_string_sql(name):
    column = "`" + name.replace("`", "``") + "`"
    return f"""CASE WHEN {column} IS NULL THEN CAST(NULL AS STRING)
        ELSE format_string('%02d:%02d:%02d.%03d',
            CAST(floor({column} / 3600000) AS INT),
            CAST(floor(pmod({column}, 3600000) / 60000) AS INT),
            CAST(floor(pmod({column}, 60000) / 1000) AS INT),
            CAST(pmod({column}, 1000) AS INT)) END"""


def read_source(archive, spark):
    from pyspark.sql import functions as F
    from pyspark.sql.types import StructType

    frame = spark.read.schema(StructType.fromJson(archive["read_schema"])).parquet(*archive["paths"])
    for name in archive["time_columns"]:
        frame = frame.withColumn(name, F.expr(time_as_string_sql(name)))
    return frame


def ingest_month(archive, args, spark):
    from pyspark.sql import functions as F

    rows = (read_source(archive, spark)
            .withColumn("variant", F.lit("standard"))
            .withColumn("archive_month", F.lit(archive["month"])))
    predicate = partition_filter(archive)
    with operation("delta.write", month=archive["month"], expected_games=archive["games"]):
        (rows.write.format("delta").mode("overwrite")
         .option("replaceWhere", predicate)
         .partitionBy("variant", "archive_month").saveAsTable(args.full_table))
    version = spark.sql(f"DESCRIBE HISTORY {args.full_table} LIMIT 1").first()["version"]
    with operation("delta.verify", month=archive["month"], delta_version=version):
        count = (spark.read.option("versionAsOf", version).table(args.full_table)
                 .where(predicate).count())
    if count != archive["games"]:
        raise ValueError(f"Row count mismatch: {count} != {archive['games']}")
    event("delta.committed", month=archive["month"], games=count, delta_version=version)
    return {**archive, "version": version}
