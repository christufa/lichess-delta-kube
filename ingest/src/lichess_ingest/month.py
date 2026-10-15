"""Turn one verified .pgn.zst into Parquet files in the destination, plus a manifest.

Layout in the destination:
  raw/games/month=YYYY-MM/part-<run>-<worker>-<n>.parquet
  _state/raw_games/YYYY-MM.json        manifest: files, row counts, checksums, stats

The manifest is written only after every file is uploaded and the row count equals
Lichess's published game count. It is the source of truth: after writing it, any
other file under the month's prefix (an earlier run, a crashed attempt) is deleted.
"""
from __future__ import annotations

import multiprocessing
import os
import queue
import shutil
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from .log import Progress, human_bytes, human_secs, log, resource_usage, self_usage
from .parse import OUTPUT_SCHEMA, chunk_to_table, chunk_to_table_reference, iter_chunks, with_lineage
from .source import MonthEntry, open_decompressed

MB = 1 << 20
TABLE_PREFIX = "raw/games"
STATE_PREFIX = "_state/raw_games"


def month_prefix(month: str) -> str:
    return f"{TABLE_PREFIX}/month={month}/"


def state_name(month: str) -> str:
    return f"{STATE_PREFIX}/{month}.json"


# --------------------------------------------------------------------------- worker process

def _worker(wid, in_q, out_q, out_dir, lineage, compression, level, target_bytes, check_parity):
    writer = tmp = sink = None
    file_idx = rows = 0

    def close_file():
        nonlocal writer, tmp, sink, file_idx, rows
        writer.close()
        sink.close()
        final = tmp[:-4]
        os.replace(tmp, final)
        out_q.put(("file", {"path": final, "rows": rows, "bytes": os.path.getsize(final)}))
        writer = tmp = sink = None
        rows = 0
        file_idx += 1

    try:
        while True:
            item = in_q.get()
            if item is None:
                break
            seq, chunk = item
            t0 = time.perf_counter()
            table, unknown, fallback = chunk_to_table(chunk)
            parse_s = time.perf_counter() - t0
            parity = chunk_to_table_reference(chunk)[0].equals(table) if (check_parity and seq == 0) else None
            table = with_lineage(table, lineage)
            t1 = time.perf_counter()
            if writer is None:
                tmp = os.path.join(out_dir, f"part-{lineage['_run_id']}-{wid:02d}-{file_idx:04d}.parquet.tmp")
                sink = pa.OSFile(tmp, "wb")  # unbuffered, so tell() is the true size written
                writer = pq.ParquetWriter(sink, OUTPUT_SCHEMA, compression=compression, compression_level=level)
            writer.write_table(table)  # one row group per chunk
            rows += table.num_rows
            write_s = time.perf_counter() - t1
            out_q.put(("chunk", {"games": table.num_rows, "parse_s": parse_s, "write_s": write_s,
                                 "unknown": dict(unknown), "fallback": fallback, "parity": parity}))
            if sink.tell() >= target_bytes:
                close_file()
        if writer is not None:
            close_file()
        out_q.put(("done", wid, os.getpid(), self_usage()))
    except BaseException:
        out_q.put(("error", wid, traceback.format_exc()))


# --------------------------------------------------------------------------- one month

def process_month(entry: MonthEntry, src_path: str, dest, *, work_dir: str, run_id: str, workers: int,
                  chunk_mb: float, file_mb: float, compression: str, level: int, upload_concurrency: int,
                  check_parity: bool = True, progress_every: float = 30.0) -> dict:
    stage = os.path.join(work_dir, "stage", entry.month)
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    lineage = {"month": entry.month, "_source_file": entry.file, "_run_id": run_id,
               "_ingested_at": datetime.now(timezone.utc)}

    ctx = multiprocessing.get_context("forkserver" if sys.platform == "linux" else "spawn")
    in_q = ctx.Queue(maxsize=workers * 2)  # backpressure: never buffer the whole month
    out_q = ctx.Queue()
    procs = [ctx.Process(target=_worker, daemon=True,
                         args=(w, in_q, out_q, stage, lineage, compression, level, int(file_mb * MB), check_parity))
             for w in range(workers)]
    for p in procs:
        p.start()

    totals = Counter()
    unknown = Counter()
    parity = None
    files: list[dict] = []
    uploads = []
    usage: dict = {}
    finished: set = set()
    done_workers = 0
    upload_pool = ThreadPoolExecutor(max_workers=upload_concurrency)
    upload_s = [0.0]

    def upload(path, rows, size):
        t = time.perf_counter()
        name = month_prefix(entry.month) + os.path.basename(path)
        dest.put_file(path, name)
        os.remove(path)
        upload_s[0] += time.perf_counter() - t
        return {"name": name, "rows": rows, "bytes": size}

    def handle(msg):
        nonlocal parity, done_workers
        kind = msg[0]
        if kind == "chunk":
            r = msg[1]
            totals["games"] += r["games"]
            totals["chunks"] += 1
            totals["fallback_chunks"] += int(r["fallback"])
            totals["parse_s"] += r["parse_s"]
            totals["write_s"] += r["write_s"]
            unknown.update(r["unknown"])
            if r["parity"] is not None:
                parity = r["parity"]
        elif kind == "file":
            f = msg[1]
            uploads.append(upload_pool.submit(upload, f["path"], f["rows"], f["bytes"]))
        elif kind == "done":
            done_workers += 1
            finished.add(msg[2])
            if msg[3]:
                usage[msg[2]] = msg[3]
        elif kind == "error":
            raise RuntimeError(f"parser worker {msg[1]} failed:\n{msg[2]}")

    def check_workers():
        crashed = [p.exitcode for p in procs if not p.is_alive() and p.pid not in finished]
        if crashed:
            raise RuntimeError(f"a parser worker exited unexpectedly (exit codes {crashed}); "
                               f"if this is -9 it was likely killed for using too much memory")

    def drain(block=False):
        if block:
            try:
                handle(out_q.get(timeout=1))
            except queue.Empty:
                check_workers()
            return
        while True:
            try:
                handle(out_q.get_nowait())
            except queue.Empty:
                return

    fh, reader = open_decompressed(src_path)
    progress = Progress(f"{entry.month} parse", os.path.getsize(src_path), every=progress_every)
    t0 = time.perf_counter()
    timings: dict = {}
    try:
        for seq, chunk in enumerate(iter_chunks(reader, int(chunk_mb * MB), timings=timings)):
            while True:
                try:
                    in_q.put((seq, chunk), timeout=1)
                    break
                except queue.Full:
                    drain()
                    check_workers()
            drain()
            progress.update(fh.tell(), games=f"{totals['games']:,}")
        for _ in procs:
            in_q.put(None)
        while done_workers < workers:
            drain(block=True)
        drain()
        parse_wall = time.perf_counter() - t0
        for fut in uploads:
            files.append(fut.result())
    except BaseException:
        for p in procs:
            p.kill()
        upload_pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        fh.close()
    upload_pool.shutdown(wait=True)
    for p in procs:
        p.join(timeout=10)
    total_wall = time.perf_counter() - t0

    rows = sum(f["rows"] for f in files)
    if parity is False:
        raise ValueError(f"{entry.month}: fast and reference parsers disagree on the first chunk")
    if rows != entry.games or totals["games"] != entry.games:
        raise ValueError(f"{entry.month}: parsed {totals['games']:,} games / {rows:,} rows in files, "
                         f"but Lichess publishes {entry.games:,}")

    files.sort(key=lambda f: f["name"])
    manifest = {
        "month": entry.month, "source_file": entry.file, "source_url": entry.url, "sha256": entry.sha256,
        "games": rows, "files": files, "parquet_bytes": sum(f["bytes"] for f in files),
        "run_id": run_id, "ingested_at": lineage["_ingested_at"].isoformat(),
        "schema": [f.name for f in OUTPUT_SCHEMA],
        "stats": {
            "parse_wall_s": round(parse_wall, 2), "total_wall_s": round(total_wall, 2),
            "games_per_s": round(rows / parse_wall) if parse_wall else None,
            "chunks": totals["chunks"], "fallback_chunks": totals["fallback_chunks"], "parity_check": parity,
            "main_decompress_read_s": round(timings.get("read_s", 0.0), 2),
            "worker_parse_s_total": round(totals["parse_s"], 2),
            "worker_write_s_total": round(totals["write_s"], 2),
            "upload_s_total": round(upload_s[0], 2),
            "unknown_tags": dict(unknown.most_common(20)),
            "workers": workers, "chunk_mb": chunk_mb, "file_mb": file_mb,
            "compression": compression, "compression_level": level,
            "resources": resource_usage(total_wall, usage),
        },
    }
    keep = {f["name"] for f in files}
    stale = [n for n in dest.list(month_prefix(entry.month)) if n not in keep]
    manifest["stats"]["stale_files_removed"] = len(stale)
    dest.put_json(state_name(entry.month), manifest)  # commit point
    for name in stale:  # only after the manifest points at the new files
        dest.delete(name)
    shutil.rmtree(stage, ignore_errors=True)

    log(f"{entry.month}: committed", games=f"{rows:,}", files=len(files),
        parquet=human_bytes(manifest["parquet_bytes"]), wall=human_secs(total_wall),
        games_per_s=f"{manifest['stats']['games_per_s']:,}")
    return manifest
