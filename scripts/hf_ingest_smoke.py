"""One-off serverless benchmark. Reads cached shards; writes only unique test tables."""
import argparse
import json
import time
from pathlib import Path


def prepare_parquet(source, destination):
    """Frozen pre-optimization baseline for comparison with production read_source."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from tqdm.auto import tqdm
    with pq.ParquetFile(source) as parquet:
        schema = pa.schema([pa.field(f.name, pa.string() if pa.types.is_time(f.type) else f.type,
                                     nullable=f.nullable) for f in parquet.schema_arrow])
        with pq.ParquetWriter(destination, schema, compression="zstd") as writer:
            with tqdm(total=parquet.metadata.num_rows, unit="rows", desc="Baseline rewrite", mininterval=5) as bar:
                for batch in parquet.iter_batches(batch_size=8192):
                    writer.write_table(pa.Table.from_batches([batch]).cast(schema))
                    bar.update(batch.num_rows)


def main():
    import pyarrow.parquet as pq
    from pyspark.sql import SparkSession, functions as F
    from lichess_pipeline.download import inspect_parquet
    from lichess_pipeline.insert import read_source
    from lichess_pipeline.observability import configure_logging, event, operation

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--table-prefix", required=True)
    args = parser.parse_args()
    # All destinations are explicitly confined to a dedicated smoke namespace.
    import re
    if not re.fullmatch(r"brikt\.lichess_dev\.hf_smoke_[0-9a-f]+", args.table_prefix):
        raise ValueError("Invalid smoke table prefix")
    if not args.scratch.startswith("/Volumes/brikt/lichess_dev/staging/smoke/"):
        raise ValueError("Invalid scratch path")
    root = Path(args.scratch)
    root.mkdir(parents=True, exist_ok=True)
    configure_logging("smoke", root.name)
    spark = SparkSession.builder.getOrCreate()
    report = {"source": args.source, "spark_version": spark.version, "results": []}
    with pq.ParquetFile(args.source) as raw:
        report["source_rows"] = raw.metadata.num_rows
        report["source_bytes"] = Path(args.source).stat().st_size
        report["arrow_schema"] = str(raw.schema_arrow)

    def save_report():
        (root / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    def measure(name, fn):
        start = time.monotonic()
        result = {"benchmark": name}
        try:
            with operation(name):
                result.update(fn())
            result["success"] = True
        except Exception as exc:
            result.update(success=False, error=str(exc)[:3000])
        result["seconds"] = round(time.monotonic() - start, 3)
        report["results"].append(result)
        save_report()
        event("benchmark.result", **result)
        return result

    def write_frame(frame, suffix):
        table = args.table_prefix + "_" + suffix
        frame.write.format("delta").mode("error").saveAsTable(table)
        count = spark.table(table).count()
        if count != report["source_rows"]:
            raise ValueError(f"Row count mismatch: {count}")
        return {"table": table, "rows": count}

    # Warming the Spark session is excluded from each measured data path.
    spark.range(1).count()
    for round_number in (1, 2):
        baseline_table = args.table_prefix + f"_baseline{round_number}"

        def baseline():
            prepared = root / f"baseline{round_number}.parquet"
            started = time.monotonic()
            prepare_parquet(args.source, prepared)
            preparation_seconds = time.monotonic() - started
            result = write_frame(spark.read.parquet(str(prepared)), f"baseline{round_number}")
            result["preparation_seconds"] = round(preparation_seconds, 3)
            return result

        base = measure(f"baseline{round_number}", baseline)
        if not base["success"]:
            raise RuntimeError("Baseline failed; see results.json")

        def validate(result):
            candidate = spark.table(result["table"])
            expected = spark.table(baseline_table)
            if candidate.columns != expected.columns:
                raise ValueError("Column order or names differ")
            # Multiset equality detects changed values and duplicate-count differences.
            if (candidate.exceptAll(expected).limit(1).count()
                    or expected.exceptAll(candidate).limit(1).count()):
                raise ValueError("Full-row multiset comparison failed")
            return {"equal": True, "table": result["table"]}

        if round_number == 1:
            def inferred():
                frame = spark.read.parquet(args.source)
                event("native.schema", schema=frame.schema.json())
                return write_frame(frame.withColumn("UTCTime", F.col("UTCTime").cast("string")), "native")
            native = measure("native_inference", inferred)
            if native["success"]:
                measure("native_equality", lambda: validate(native))

        def explicit():
            metadata = inspect_parquet(args.source)
            converted = read_source({**metadata, "paths": [args.source]}, spark)
            return write_frame(converted, f"explicit{round_number}")

        direct = measure(f"explicit{round_number}", explicit)
        if direct["success"]:
            measure(f"explicit_equality{round_number}", lambda: validate(direct))
    report["complete"] = True
    save_report()
    event("benchmark.complete", report=report)


if __name__ == "__main__":
    main()
