"""Shared task arguments, volume paths and manifest I/O."""
import argparse
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from .observability import configure_logging, event, operation


SOURCE_REPO = "Lichess/standard-chess-games"
FORMAT_VERSION = "hf-parquet-v1"


def validate_period(year, month):
    if (year, month) != (0, 0) and (year < 2013 or not 1 <= month <= 12):
        raise ValueError("Set both --year (>=2013) and --month (1..12), or leave both 0 for all published months")


def partition_filter(archive):
    # Values originate in validated HF partition paths, not arbitrary SQL.
    if not re.fullmatch(r"\w+", archive["variant"]) or not re.fullmatch(r"\d{4}-\d{2}", archive["month"]):
        raise ValueError("Invalid archive partition")
    return f"variant = '{archive['variant']}' AND archive_month = '{archive['month']}'"

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

def history_directory(root, args, archive):
    partition_filter(archive)  # Validate components before constructing paths.
    destination = json.dumps([FORMAT_VERSION, SOURCE_REPO, args.full_table])
    identity = hashlib.sha256(destination.encode()).hexdigest()[:24]
    return root.parent.parent / "history" / identity / archive["variant"] / archive["month"]


def checkpoint(root, args, archive, stage):
    path = history_directory(root, args, archive) / f"{stage}.json"
    if path.exists():
        record = read_json(path)
        if (record["table"] != args.full_table or record["source_repo"] != SOURCE_REPO
                or record["stage"] != stage):
            raise ValueError("Checkpoint destination mismatch")
        if record["archive"]["fingerprint"] != archive["fingerprint"]:
            return None  # Upstream replaced or added a shard in this month.
        event("checkpoint.reused", checkpoint_stage=stage, month=archive["month"],
              original_run_id=record["run_id"])
        return record["archive"]
    return None


def record_completion(root, args, archive, stage):
    record = {"table": args.full_table, "source_repo": SOURCE_REPO, "stage": stage,
              "run_id": root.name, "completed_at": datetime.now(timezone.utc).isoformat(),
              "archive": archive}
    write_json(history_directory(root, args, archive) / f"{stage}.json", record)
    event("checkpoint.saved", checkpoint_stage=stage, variant=archive["variant"],
          month=archive["month"], games=archive.get("games"),
          delta_version=archive.get("version"))


def run_task(handler, stage):
    parser = argparse.ArgumentParser(description=f"Lichess pipeline: {stage}")
    for name in ("catalog", "schema", "volume", "run-id", "table"):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--year", type=int, default=0)
    parser.add_argument("--month", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0, help="Maximum pending months; 0 means all")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    for name in ("catalog", "schema", "volume", "run_id", "table", "variant"):
        if hasattr(args, name) and not re.fullmatch(r"[A-Za-z0-9_\-]+", getattr(args, name)):
            parser.error(f"Invalid {name}")
    if args.limit < 0 or not 1 <= args.workers <= 16:
        parser.error("limit must be nonnegative and workers must be between 1 and 16")
    try:
        validate_period(args.year, args.month)
    except ValueError as exc:
        parser.error(str(exc))
    configure_logging(stage, args.run_id)
    with operation("task", stage_name=stage):
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        namespace = f"`{args.catalog}`.`{args.schema}`"
        if hasattr(args, "table"):
            args.full_table = f"{namespace}.`{args.table}`"
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {namespace}")
        spark.sql(f"CREATE VOLUME IF NOT EXISTS {namespace}.`{args.volume}`")
        root = Path(f"/Volumes/{args.catalog}/{args.schema}/{args.volume}/runs/{args.run_id}")
        root.mkdir(parents=True, exist_ok=True)
        event("storage.ready", root=str(root), table=args.full_table)
        handler(args, spark, root)
