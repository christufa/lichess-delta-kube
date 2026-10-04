"""Incrementally sync all published HF months, committing one month at a time."""
import time
from tqdm.auto import tqdm

from .common import (FORMAT_VERSION, SOURCE_REPO, checkpoint, read_json,
                     record_completion, write_json)
from .download import discover_months, stage_month
from .insert import ingest_month
from .observability import event, operation


def sync_dataset(args, spark, root):
    selection = {"year": args.year, "month": args.month, "table": args.full_table,
                 "source_repo": SOURCE_REPO, "format": FORMAT_VERSION, "limit": args.limit}
    plan_path = root / "hf-plan.json"
    if plan_path.exists():
        plan = read_json(plan_path)
        if plan["selection"] != selection:
            raise ValueError("Run parameters changed; start a new run instead of repairing this one")
        archives = plan["archives"]
    else:
        with operation("source.discover", source_repo=SOURCE_REPO):
            discovered = discover_months(args.year, args.month)
        archives = [a for a in discovered if not checkpoint(root, args, a, "insert")]
        if args.limit:
            archives = archives[:args.limit]
        write_json(plan_path, {"selection": selection, "archives": archives})
        event("sync.selection", published_months=len(discovered), pending_months=len(archives))
    event("sync.plan", months=len(archives), source_repo=SOURCE_REPO)
    results = []
    started = time.monotonic()
    with tqdm(total=len(archives), desc="HF months", unit="month", position=0,
              mininterval=5) as progress:
        for archive in archives:
            saved = checkpoint(root, args, archive, "insert")
            if saved is None:
                staged = stage_month(archive, args, root)
                saved = ingest_month(staged, args, spark)
                record_completion(root, args, saved, "insert")
            results.append(saved)
            write_json(root / "delta.json", results)
            progress.update(1)
            event("sync.progress", completed_months=len(results), total_months=len(archives),
                  games=sum(a["games"] for a in results), elapsed_seconds=round(time.monotonic() - started, 2))
    write_json(root / "delta.json", results)
    event("sync.summary", months=len(results), games=sum(a["games"] for a in results))
