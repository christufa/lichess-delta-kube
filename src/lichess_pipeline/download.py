"""Discover immutable HF snapshots and stage Parquet without PGN parsing."""
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.hf_api import RepoFile
from tqdm.auto import tqdm

from .common import SOURCE_REPO, read_json, write_json
from .observability import event, operation

FILE_PATTERN = re.compile(r"data/year=(\d{4})/month=(\d{2})/train-(\d+)-of-(\d+)\.parquet")


def discover_months(year=0, month=0):
    api = HfApi(token=False)
    revision = api.dataset_info(SOURCE_REPO).sha
    groups = {}
    for item in api.list_repo_tree(SOURCE_REPO, repo_type="dataset", revision=revision,
                                   path_in_repo="data", recursive=True):
        if not isinstance(item, RepoFile):
            continue
        match = FILE_PATTERN.fullmatch(item.path)
        if not match:
            if item.path.endswith(".parquet"):
                raise ValueError(f"Unexpected source Parquet path: {item.path}")
            continue
        y, m, index, total = map(int, match.groups())
        if y < 2013 or not 1 <= m <= 12:
            raise ValueError(f"Invalid source partition: {item.path}")
        if year and (y, m) != (year, month):
            continue
        groups.setdefault(f"{y:04d}-{m:02d}", []).append({
            "path": item.path, "size": item.size,
            "oid": item.lfs.sha256 if item.lfs else item.blob_id,
            "index": index, "total": total})
    if not groups:
        raise ValueError("No published HF Parquet files match the requested month")
    archives = []
    for period, files in sorted(groups.items()):
        files.sort(key=lambda item: item["path"])
        total = files[0]["total"]
        if (any(f["total"] != total for f in files)
                or len(files) != total or {f["index"] for f in files} != set(range(total))):
            raise ValueError(f"Incomplete HF shard set for {period}; retry after publication finishes")
        fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        archives.append({"variant": "standard", "month": period, "revision": revision,
                         "fingerprint": fingerprint, "files": files})
    event("source.discovered", source_repo=SOURCE_REPO, revision=revision,
          months=len(archives), first_month=archives[0]["month"], last_month=archives[-1]["month"],
          files=sum(len(a["files"]) for a in archives),
          bytes=sum(f["size"] for a in archives for f in a["files"]))
    return archives


def prepare_parquet(source, destination, progress_position=1):
    """Stream columnar batches; convert Arrow time types unsupported by older Spark."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    destination = Path(destination)
    marker = destination.with_suffix(".json")
    if destination.exists() and marker.exists():
        saved = read_json(marker)
        if destination.stat().st_size == saved["bytes"]:
            with pq.ParquetFile(destination) as cached:
                if cached.metadata.num_rows == saved["games"]:
                    return saved["games"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".partial")
    started = last_log = time.monotonic()
    rows = 0
    with pq.ParquetFile(source) as parquet:
        schema = parquet.schema_arrow.remove_metadata()
        required = {"Site", "UTCDate", "UTCTime", "movetext"}
        if not required.issubset(schema.names):
            raise ValueError(f"Missing HF fields: {sorted(required - set(schema.names))}")
        if {"variant", "archive_month"}.intersection(schema.names):
            raise ValueError("HF fields conflict with pipeline partition columns")
        schema = pa.schema([pa.field(f.name, pa.string() if pa.types.is_time(f.type) else f.type,
                                     nullable=f.nullable) for f in schema])
        with pq.ParquetWriter(temporary, schema, compression="zstd") as writer, tqdm(
                total=parquet.metadata.num_rows, desc=f"Prepare {destination.name}", unit="rows",
                position=progress_position, leave=False, mininterval=5) as progress:
            for batch in parquet.iter_batches(batch_size=8192):
                table = pa.Table.from_batches([batch]).cast(schema)
                writer.write_table(table)
                rows += batch.num_rows
                progress.update(batch.num_rows)
                now = time.monotonic()
                if now - last_log >= 30:
                    event("parquet.prepare.progress", file=destination.name, games=rows,
                          total_games=parquet.metadata.num_rows,
                          games_per_second=round(rows / max(now - started, 0.001), 2))
                    last_log = now
        if rows != parquet.metadata.num_rows or not rows:
            raise ValueError("Prepared Parquet row count mismatch or empty shard")
    temporary.replace(destination)
    write_json(marker, {"games": rows, "bytes": destination.stat().st_size})
    return rows


def stage_month(archive, args, root):
    cache = root.parent.parent / "hf" / archive["fingerprint"]
    output = cache / "prepared"
    started = time.monotonic()
    positions = Queue()
    for position in range(2, args.workers + 2):
        positions.put(position)

    def stage_file(item):
        source = hf_hub_download(SOURCE_REPO, item["path"], repo_type="dataset",
                                 revision=archive["revision"], local_dir=str(cache / "raw"), token=False)
        if Path(source).stat().st_size != item["size"]:
            raise ValueError(f"Downloaded size mismatch: {item['path']}")
        target = output / Path(item["path"]).name
        position = positions.get()
        try:
            count = prepare_parquet(source, target, progress_position=position)
        finally:
            positions.put(position)
        return str(target), count, item["size"]

    paths, games, completed_bytes = [], 0, 0
    total_bytes = sum(f["size"] for f in archive["files"])
    with operation("month.download", month=archive["month"], files=len(archive["files"]),
                   total_bytes=total_bytes), ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(stage_file, item) for item in archive["files"]]
        with tqdm(total=len(futures), desc=f"Files {archive['month']}", unit="file",
                  position=1, mininterval=5) as progress:
            for future in as_completed(futures):
                try:
                    path, count, size = future.result()
                except Exception:
                    for pending in futures:
                        pending.cancel()
                    raise
                paths.append(path)
                games += count
                completed_bytes += size
                progress.update(1)
                elapsed = max(time.monotonic() - started, 0.001)
                event("download.progress", month=archive["month"], files=len(paths),
                      total_files=len(futures), bytes=completed_bytes, total_bytes=total_bytes,
                      games=games, bytes_per_second=round(completed_bytes / elapsed))
    return {**archive, "paths": sorted(paths), "games": games}
