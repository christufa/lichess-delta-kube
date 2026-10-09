"""pgn-timing: measure each stage of turning a Lichess .pgn.zst into Parquet/Iceberg.

Stages (each runnable on its own):
  download    fetch the .pgn.zst (resumable)
  decompress  stream-decompress only, to measure zstd throughput
  parse       decompress -> game-aligned chunks -> parallel header parse -> Parquet
  commit      register the Parquet files in a local Iceberg table (PyIceberg add_files)
  pychess     old-style python-chess parsing on a sample, for comparison
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import sys
import time
from collections import Counter
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from .parse import iter_chunks, process_chunk
from .source import download, open_decompressed

MB = 1 << 20


def _env() -> dict:
    return {"cpus": os.cpu_count(), "python": platform.python_version(), "platform": platform.platform()}


def cmd_download(args) -> dict:
    return download(args.url, args.out)


def cmd_decompress(args) -> dict:
    compressed, reader = open_decompressed(args.src, _limit(args))
    t0 = time.perf_counter()
    while reader.read(16 * MB):
        pass
    elapsed = time.perf_counter() - t0
    return {"stage": "decompress", "src": args.src, "compressed_bytes": compressed.bytes,
            "decompressed_bytes": reader.bytes, "seconds": round(elapsed, 2),
            "decompressed_mb_per_s": round(reader.bytes / elapsed / 1e6, 1)}


def cmd_parse(args) -> dict:
    if args.out:
        os.makedirs(args.out, exist_ok=True)
    compressed, reader = open_decompressed(args.src, _limit(args))
    timings: dict = {}
    totals = Counter()
    unknown = Counter()
    max_inflight = args.workers * 2
    t0 = time.perf_counter()
    wait_s = 0.0
    last_report = t0

    def collect(done):
        for fut in done:
            r = fut.result()
            for k in ("games", "in_bytes", "out_bytes", "parse_s", "write_s"):
                totals[k] += r[k]
            totals["files"] += 1
            unknown.update(r["unknown_tags"])

    # forkserver avoids fork-after-threads deadlocks (zstd/pyarrow start threads)
    ctx = multiprocessing.get_context("forkserver" if sys.platform == "linux" else "spawn")
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as pool:
        pending = set()
        for seq, chunk in enumerate(iter_chunks(reader, args.chunk_mb * MB, timings=timings)):
            while len(pending) >= max_inflight:  # backpressure: never buffer the whole month
                w0 = time.perf_counter()
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                wait_s += time.perf_counter() - w0
                collect(done)
            pending.add(pool.submit(process_chunk, seq, chunk, args.out, args.compression))
            now = time.perf_counter()
            if now - last_report >= 10:
                last_report = now
                print(f"  {reader.bytes / 1e9:.2f} GB decompressed, {totals['games']:,} games parsed, "
                      f"{reader.bytes / (now - t0) / 1e6:.0f} MB/s", file=sys.stderr, flush=True)
        w0 = time.perf_counter()
        done, _ = wait(pending)
        wait_s += time.perf_counter() - w0
        collect(done)
    elapsed = time.perf_counter() - t0

    return {
        "stage": "parse", "src": args.src, "workers": args.workers, "chunk_mb": args.chunk_mb,
        "compressed_bytes": compressed.bytes, "decompressed_bytes": reader.bytes,
        "games": totals["games"], "files": totals["files"], "parquet_bytes": totals["out_bytes"],
        "wall_s": round(elapsed, 2),
        "games_per_s": round(totals["games"] / elapsed) if elapsed else None,
        "decompressed_mb_per_s": round(reader.bytes / elapsed / 1e6, 1) if elapsed else None,
        # Where the time went. Main process: decompress+read; workers: parse and Parquet encode.
        "main_decompress_read_s": round(timings.get("read_s", 0.0), 2),
        "main_waiting_on_workers_s": round(wait_s, 2),
        "worker_parse_s_total": round(totals["parse_s"], 2),
        "worker_write_s_total": round(totals["write_s"], 2),
        "bottleneck_hint": _hint(timings.get("read_s", 0.0), wait_s, elapsed),
        "unknown_tags": dict(unknown.most_common(20)),
    }


def _hint(read_s: float, wait_s: float, wall: float) -> str:
    if wall <= 0:
        return "n/a"
    if wait_s / wall > 0.5:
        return "workers (parse/write) — add cores or a faster parser"
    if read_s / wall > 0.5:
        return "decompression/input — single zstd stream or download speed"
    return "balanced"


def cmd_commit(args) -> dict:
    import pyarrow.parquet as pq
    from pyiceberg.catalog.sql import SqlCatalog

    files = [p.resolve().as_posix() for p in sorted(Path(args.parquet_dir).glob("*.parquet"))]
    if not files:
        raise SystemExit(f"no parquet files in {args.parquet_dir}")
    os.makedirs(args.warehouse, exist_ok=True)
    wh = Path(args.warehouse).resolve()
    # as_posix()/as_uri() keep these valid on Windows (C:/... and file:///C:/...)
    catalog = SqlCatalog("spike", uri=f"sqlite:///{(wh / 'catalog.db').as_posix()}", warehouse=wh.as_uri())
    catalog.create_namespace_if_not_exists("raw")
    ident = "raw.games_spike"
    if catalog.table_exists(ident):
        catalog.drop_table(ident)
    table = catalog.create_table(ident, schema=pq.read_schema(files[0]))
    t0 = time.perf_counter()
    table.add_files(files)
    elapsed = time.perf_counter() - t0
    rows = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
    snapshot = table.refresh().current_snapshot()
    return {"stage": "commit", "files": len(files), "rows": rows, "seconds": round(elapsed, 2),
            "snapshot_id": snapshot.snapshot_id if snapshot else None,
            "snapshot_added_records": snapshot.summary.get("added-records") if snapshot else None}


def cmd_pychess(args) -> dict:
    try:
        import chess.pgn
    except ImportError:
        raise SystemExit("python-chess is not installed: pip install chess")
    import io

    _, reader = open_decompressed(args.src)
    text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
    t0 = time.perf_counter()
    n = 0
    while n < args.games and chess.pgn.read_game(text) is not None:
        n += 1  # read_game parses headers AND replays every move
    elapsed = time.perf_counter() - t0
    return {"stage": "pychess", "games": n, "seconds": round(elapsed, 2),
            "games_per_s": round(n / elapsed) if elapsed else None}


def _limit(args):
    return args.limit_mb * MB if getattr(args, "limit_mb", None) else None


def main(argv=None):
    p = argparse.ArgumentParser(prog="pgn-timing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report", help="append the JSON result to this file (one line per run)")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download", help="resumable download of a .pgn.zst")
    d.add_argument("url")
    d.add_argument("--out", required=True)
    d.set_defaults(fn=cmd_download)

    z = sub.add_parser("decompress", help="stream-decompress only")
    z.add_argument("src", help="local .pgn.zst path or http(s) URL")
    z.add_argument("--limit-mb", type=int, help="stop after this many decompressed MB")
    z.set_defaults(fn=cmd_decompress)

    pa = sub.add_parser("parse", help="decompress + parallel header parse to Parquet")
    pa.add_argument("src", help="local .pgn.zst path or http(s) URL")
    pa.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    pa.add_argument("--chunk-mb", type=int, default=64, help="decompressed MB per chunk")
    pa.add_argument("--out", help="directory for Parquet files (omit to only time encoding)")
    pa.add_argument("--compression", default="zstd")
    pa.add_argument("--limit-mb", type=int, help="stop after this many decompressed MB")
    pa.set_defaults(fn=cmd_parse)

    c = sub.add_parser("commit", help="register Parquet files in a local Iceberg table")
    c.add_argument("parquet_dir")
    c.add_argument("--warehouse", required=True, help="local directory for the Iceberg warehouse")
    c.set_defaults(fn=cmd_commit)

    pc = sub.add_parser("pychess", help="python-chess baseline on a sample of games")
    pc.add_argument("src")
    pc.add_argument("--games", type=int, default=20_000)
    pc.set_defaults(fn=cmd_pychess)

    args = p.parse_args(argv)
    result = {**args.fn(args), "env": _env(), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    print(json.dumps(result, indent=2))
    if args.report:
        with open(args.report, "a") as fh:
            fh.write(json.dumps(result) + "\n")
    return result


if __name__ == "__main__":
    main()
