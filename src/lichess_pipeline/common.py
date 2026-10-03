"""Shared task arguments, volume paths and manifest I/O."""
import argparse
import json
import hashlib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from .observability import configure_logging, event, operation


def selected_period(year, month):
    if year == 0 and month == 0:
        previous = datetime.now(ZoneInfo("America/New_York")).date().replace(day=1) - timedelta(days=1)
        return previous.year, previous.month
    if year < 2013 or not 1 <= month <= 12:
        raise ValueError("Set both --year (>=2013) and --month (1..12), or leave both 0")
    return year, month

def partition_filter(archive):
    # Values originate in the validated archive filename, not arbitrary SQL.
    if not re.fullmatch(r"\w+", archive["variant"]) or not re.fullmatch(r"\d{4}-\d{2}", archive["month"]):
        raise ValueError("Invalid archive partition")
    return f"variant = '{archive['variant']}' AND archive_month = '{archive['month']}'"

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

def history_directory(root, args, archive):
    partition_filter(archive)  # Validate components before constructing paths.
    destination = json.dumps([args.full_table, args.repo])
    identity = hashlib.sha256(destination.encode()).hexdigest()[:24]
    return root.parent.parent / "history" / identity / archive["variant"] / archive["month"]


def checkpoint(root, args, archive, stage):
    path = history_directory(root, args, archive) / f"{stage}.json"
    if path.exists():
        record = read_json(path)
        if record["table"] != args.full_table or record["repo"] != args.repo:
            raise ValueError("Checkpoint destination mismatch")
        event("checkpoint.reused", checkpoint_stage=stage, variant=archive["variant"],
              month=archive["month"], original_run_id=record["run_id"])
        return record["archive"]
    return None


def record_completion(root, args, archive, stage):
    record = {"table": args.full_table, "repo": args.repo, "stage": stage,
              "run_id": root.name, "completed_at": datetime.now(timezone.utc).isoformat(),
              "archive": archive}
    write_json(history_directory(root, args, archive) / f"{stage}.json", record)
    event("checkpoint.saved", checkpoint_stage=stage, variant=archive["variant"],
          month=archive["month"], games=archive.get("games"),
          delta_version=archive.get("version"), hf_commit=archive.get("hf_commit"))


def run_task(handler, stage):
    parser = argparse.ArgumentParser(description=f"Lichess pipeline: {stage}")
    for name in ("catalog", "schema", "volume", "run-id", "table", "repo"):
        parser.add_argument(f"--{name}", required=True)
    if stage == "download":
        parser.add_argument("--variant", default="standard")
        parser.add_argument("--year", type=int, default=0)
        parser.add_argument("--month", type=int, default=0)
        parser.add_argument("--limit", type=int, default=1)
    if stage == "upload":
        for name in ("secret-scope", "secret-key"):
            parser.add_argument(f"--{name}", required=True)
        parser.add_argument("--shard-size", type=int, default=200000)
    args = parser.parse_args()
    for name in ("catalog", "schema", "volume", "run_id", "table", "variant"):
        if hasattr(args, name) and not re.fullmatch(r"[A-Za-z0-9_\-]+", getattr(args, name)):
            parser.error(f"Invalid {name}")
    if stage == "download":
        if args.limit < 0:
            parser.error("limit must be nonnegative")
        selected_period(args.year, args.month)
    if stage == "upload" and args.shard_size <= 0:
        parser.error("shard-size must be positive")
    configure_logging(stage, args.run_id)
    with operation("task", stage_name=stage):
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
        namespace = f"`{args.catalog}`.`{args.schema}`"
        if hasattr(args, "table"):
            args.full_table = f"{namespace}.`{args.table}`"
        if stage == "download":
            spark.sql(f"CREATE SCHEMA IF NOT EXISTS {namespace}")
            spark.sql(f"CREATE VOLUME IF NOT EXISTS {namespace}.`{args.volume}`")
        root = Path(f"/Volumes/{args.catalog}/{args.schema}/{args.volume}/runs/{args.run_id}")
        root.mkdir(parents=True, exist_ok=True)
        event("storage.ready", root=str(root), table=args.full_table, repo=args.repo)
        handler(args, spark, root)
