"""Parse staged PGN games with Spark and replace their Delta partitions."""
import io

from common import read_json, write_json, partition_filter, run_task, checkpoint, record_completion
from schema import STRING_COLUMNS, INT_COLUMNS, COLUMNS

def parse_batches(batches):
    import chess.pgn
    import pandas as pd
    from game_parser import _game_to_row
    for batch in batches:
        rows = []
        for text in batch.pgn:
            game = chess.pgn.read_game(io.StringIO(text))
            if game is None or game.errors:
                raise ValueError("Invalid PGN; refusing to commit an incomplete month")
            rows.append(_game_to_row(game))
            if len(rows) == 256:
                yield pd.DataFrame(rows, columns=COLUMNS)
                rows = []
        if rows:
            yield pd.DataFrame(rows, columns=COLUMNS)


def ingest(args, spark, root):
    archives = read_json(root / "archives.json")
    results, pending = [], []
    for archive in archives:
        saved = checkpoint(root, args, archive, "insert")
        if saved:
            results.append(saved)
        else:
            pending.append(archive)
    if not pending:
        write_json(root / "delta.json", results)
        return
    from pyspark.sql import functions as F
    from pyspark.sql.types import StructType, StructField, StringType, ShortType, IntegerType, TimestampType
    schema = StructType([StructField(c, StringType()) for c in STRING_COLUMNS]
                        + [StructField(c, IntegerType() if c == "initial_time_secs" else ShortType())
                           for c in INT_COLUMNS]
                        + [StructField("played_at", TimestampType())])
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.files.maxPartitionBytes", str(64 * 1024 * 1024))
    for archive in pending:
        raw = spark.read.schema("pgn STRING").option("mode", "FAILFAST").json(archive["path"])
        rows = (raw.mapInPandas(parse_batches, schema)
                .withColumn("variant", F.lit(archive["variant"]))
                .withColumn("archive_month", F.lit(archive["month"])))
        predicate = partition_filter(archive)
        (rows.write.format("delta").mode("overwrite")
             .option("replaceWhere", predicate)
             .partitionBy("variant", "archive_month").saveAsTable(args.full_table))
        version = spark.sql(f"DESCRIBE HISTORY {args.full_table} LIMIT 1").first()["version"]
        saved = spark.read.option("versionAsOf", version).table(args.full_table).where(predicate)
        count = saved.count()
        if count != archive["games"]:
            raise ValueError(f"Row count mismatch: {count} != {archive['games']}")
        completed = {**archive, "version": version}
        record_completion(root, args, completed, "insert")
        results.append(completed)
    write_json(root / "delta.json", results)


if __name__ == "__main__":
    run_task(ingest, "insert")
