"""Discover immutable HF snapshots and stage Parquet without PGN parsing."""
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.hf_api import RepoFile
from tqdm.auto import tqdm

from .common import SOURCE_REPO
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


def inspect_parquet(source):
    """Read only the footer to obtain row counts and an explicit Spark schema."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    with pq.ParquetFile(source) as parquet:
        schema = parquet.schema_arrow
        required = {"Site", "UTCDate", "UTCTime", "movetext"}
        if not required.issubset(schema.names):
            raise ValueError(f"Missing HF fields: {sorted(required - set(schema.names))}")
        if {"variant", "archive_month"}.intersection(schema.names):
            raise ValueError("HF fields conflict with pipeline partition columns")
        mapping = {pa.string(): "string", pa.large_string(): "string",
                   pa.int8(): "byte", pa.int16(): "short", pa.int32(): "integer",
                   pa.int64(): "long", pa.float32(): "float", pa.float64(): "double",
                   pa.bool_(): "boolean", pa.date32(): "date", pa.binary(): "binary"}
        fields, time_columns = [], []
        for field in schema:
            if field.type == pa.time32("ms"):
                # TIME_MILLIS is physically INT32; bypass unsupported inference.
                spark_type = "integer"
                time_columns.append(field.name)
            else:
                spark_type = mapping.get(field.type)
                if spark_type is None:
                    raise ValueError(f"Unsupported HF field type: {field.name}: {field.type}")
            fields.append({"name": field.name, "type": spark_type,
                           "nullable": field.nullable, "metadata": {}})
        if not parquet.metadata.num_rows:
            raise ValueError("Empty HF Parquet shard")
        return {"games": parquet.metadata.num_rows,
                "read_schema": {"type": "struct", "fields": fields},
                "time_columns": time_columns}


def stage_month(archive, args, root):
    cache = root.parent.parent / "hf" / archive["fingerprint"]
    started = time.monotonic()
    def stage_file(item):
        source = hf_hub_download(SOURCE_REPO, item["path"], repo_type="dataset",
                                 revision=archive["revision"], local_dir=str(cache / "raw"), token=False)
        if Path(source).stat().st_size != item["size"]:
            raise ValueError(f"Downloaded size mismatch: {item['path']}")
        metadata = inspect_parquet(source)
        return str(source), metadata, item["size"]

    paths, games, completed_bytes = [], 0, 0
    metadata_schema = None
    time_columns = None
    total_bytes = sum(f["size"] for f in archive["files"])
    with operation("month.download", month=archive["month"], files=len(archive["files"]),
                   total_bytes=total_bytes), ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(stage_file, item) for item in archive["files"]]
        with tqdm(total=len(futures), desc=f"Files {archive['month']}", unit="file",
                  position=1, mininterval=5) as progress:
            for future in as_completed(futures):
                try:
                    path, metadata, size = future.result()
                except Exception:
                    for pending in futures:
                        pending.cancel()
                    raise
                if metadata_schema is None:
                    metadata_schema = metadata["read_schema"]
                    time_columns = metadata["time_columns"]
                elif (metadata_schema != metadata["read_schema"]
                      or time_columns != metadata["time_columns"]):
                    for pending in futures:
                        pending.cancel()
                    raise ValueError("Inconsistent HF shard schemas within month")
                paths.append(path)
                games += metadata["games"]
                completed_bytes += size
                progress.update(1)
                elapsed = max(time.monotonic() - started, 0.001)
                event("download.progress", month=archive["month"], files=len(paths),
                      total_files=len(futures), bytes=completed_bytes, total_bytes=total_bytes,
                      games=games, bytes_per_second=round(completed_bytes / elapsed))
    event("download.summary", month=archive["month"], games=games, bytes=completed_bytes,
          elapsed_seconds=round(time.monotonic() - started, 2), rewritten_bytes=0)
    return {**archive, "paths": sorted(paths), "games": games,
            "read_schema": metadata_schema, "time_columns": time_columns}
