"""Load prepared HF Parquet with Spark and verify the committed Delta partition."""
from .common import partition_filter
from .observability import event, operation


def ingest_month(archive, args, spark):
    from pyspark.sql import functions as F

    rows = (spark.read.option("mergeSchema", "true").parquet(*archive["paths"])
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
