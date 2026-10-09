"""pgn-timing: measure each stage of turning a Lichess .pgn.zst into Parquet/Iceberg.

Stages (each runnable on its own):
  download    fetch the .pgn.zst (resumable, retried, sha256-verified)
  decompress  stream-decompress only, to measure zstd throughput
  parse       decompress -> game-aligned chunks -> parallel header parse -> Parquet
  commit      register the Parquet files in a local Iceberg table (PyIceberg add_files)
  pychess     old-style python-chess parsing on a sample, for comparison

Progress goes to stderr; stdout carries only the final JSON result, which is also
appended to --report (including failed and interrupted runs).
"""
from __future__ import annotations

import argparse
import io
import json
import multiprocessing
import os
import platform
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .log import Progress, human_bytes, human_secs, log, resource_usage
from .parse import iter_chunks, process_chunk
from .source import download, open_decompressed, open_text_stream

MB = 1 << 20
MANIFEST = "manifest.json"


def _env() -> dict:
    try:
        pkg = version("pgn-timing")
    except PackageNotFoundError:  # pragma: no cover
        pkg = "unknown"
    return {"cpus": os.cpu_count(), "python": platform.python_version(),
            "platform": platform.platform(), "pgn_timing": pkg}


def _limit(args):
    return args.limit_mb * MB if getattr(args, "limit_mb", None) else None


# --------------------------------------------------------------------------- commands

def cmd_download(args, stats: dict) -> None:
    stats.update(url=args.url, out=args.out)
    download(args.url, args.out, stats, retries=args.retries, verify=not args.no_verify,
             checksums_url=args.checksums_url, progress_every=args.log_every)


def cmd_decompress(args, stats: dict) -> None:
    compressed, reader = open_decompressed(args.src, _limit(args))
    stats.update(src=args.src, compressed_total_bytes=compressed.total)
    log("decompress started", src=args.src, size=human_bytes(compressed.total))
    progress = Progress("decompress", compressed.total, every=args.log_every)
    t0 = time.perf_counter()
    while reader.read(16 * MB):
        stats.update(compressed_bytes=compressed.bytes, decompressed_bytes=reader.bytes)
        progress.update(compressed.bytes, decompressed=human_bytes(reader.bytes))
    elapsed = time.perf_counter() - t0
    stats.update(compressed_bytes=compressed.bytes, decompressed_bytes=reader.bytes,
                 seconds=round(elapsed, 2),
                 decompressed_mb_per_s=round(reader.bytes / elapsed / 1e6, 1) if elapsed else None,
                 compression_ratio=round(reader.bytes / compressed.bytes, 2) if compressed.bytes else None)


def _prepare_out_dir(out: str, overwrite: bool) -> None:
    path = Path(out)
    path.mkdir(parents=True, exist_ok=True)
    stale = [p for p in path.iterdir() if p.suffix in (".parquet", ".tmp") or p.name == MANIFEST]
    if not stale:
        return
    if not overwrite:
        raise FileExistsError(f"{out} already contains {len(stale)} output file(s) from an earlier run; "
                              f"use a new --out directory or pass --overwrite")
    for p in stale:
        p.unlink()
    log("removed previous output", dir=out, files=len(stale))


def cmd_parse(args, stats: dict) -> None:
    if args.out:
        _prepare_out_dir(args.out, args.overwrite)
    compressed, reader = open_decompressed(args.src, _limit(args))
    max_inflight = args.workers * 2
    # Measured: each worker ~150 MB baseline + ~8x its chunk while parsing; main holds the in-flight chunks.
    est_peak = args.workers * (150 * MB + 8 * args.chunk_mb * MB) + max_inflight * args.chunk_mb * MB + 200 * MB
    stats.update(src=args.src, workers=args.workers, chunk_mb=args.chunk_mb,
                 compressed_total_bytes=compressed.total)
    log("parse started", src=args.src, size=human_bytes(compressed.total), workers=args.workers,
        chunk_mb=args.chunk_mb, est_peak_memory=human_bytes(est_peak))

    timings: dict = {}
    totals = Counter()
    unknown = Counter()
    files: list[dict] = []
    parity = {"checked": False, "equal": None}
    worker_usage: dict = stats.setdefault("_worker_usage", {})
    progress = Progress("parse", compressed.total, every=args.log_every)
    t0 = time.perf_counter()
    wait_s = 0.0

    def collect(done):
        for fut in done:
            r = fut.result()  # re-raises worker errors, including BrokenProcessPool
            for k in ("games", "in_bytes", "out_bytes", "parse_s", "write_s"):
                totals[k] += r[k]
            totals["chunks"] += 1
            totals["fallback_chunks"] += int(r["fallback"])
            unknown.update(r["unknown_tags"])
            if r["parity"] is not None:
                parity.update(checked=True, equal=r["parity"])
                if not r["parity"]:
                    log("fast and reference parsers disagree on chunk 0", "WARN")
            if r["usage"]:
                worker_usage[r["pid"]] = r["usage"]  # cumulative per process; keep the latest
            if r["file"]:
                files.append({"file": r["file"], "rows": r["games"], "bytes": r["out_bytes"]})
        stats.update(games=totals["games"], chunks=totals["chunks"], decompressed_bytes=reader.bytes,
                     compressed_bytes=compressed.bytes)

    # forkserver avoids fork-after-threads deadlocks (zstd/pyarrow start threads)
    ctx = multiprocessing.get_context("forkserver" if sys.platform == "linux" else "spawn")
    pool = ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx)
    pending: set = set()
    try:
        for seq, chunk in enumerate(iter_chunks(reader, args.chunk_mb * MB, timings=timings)):
            while len(pending) >= max_inflight:  # backpressure: never buffer the whole month
                w0 = time.perf_counter()
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                wait_s += time.perf_counter() - w0
                collect(done)
            check = seq == 0 and not args.no_parity_check
            pending.add(pool.submit(process_chunk, seq, chunk, args.out, args.compression, check))
            elapsed = time.perf_counter() - t0
            progress.update(compressed.bytes, games=f"{totals['games']:,}",
                            games_per_s=f"{totals['games'] / elapsed:,.0f}" if elapsed else "?",
                            decompressed=human_bytes(reader.bytes), inflight=len(pending))
        w0 = time.perf_counter()
        done, pending = wait(pending)
        wait_s += time.perf_counter() - w0
        collect(done)
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    elapsed = time.perf_counter() - t0
    read_s = timings.get("read_s", 0.0)

    if args.out:
        files.sort(key=lambda f: f["file"])
        manifest = {"source": args.src, "games": totals["games"], "files": files,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        Path(args.out, MANIFEST).write_text(json.dumps(manifest, indent=2))

    stats.update(
        compressed_bytes=compressed.bytes, decompressed_bytes=reader.bytes,
        games=totals["games"], chunks=totals["chunks"], files=len(files), parquet_bytes=totals["out_bytes"],
        wall_s=round(elapsed, 2),
        games_per_s=round(totals["games"] / elapsed) if elapsed else None,
        decompressed_mb_per_s=round(reader.bytes / elapsed / 1e6, 1) if elapsed else None,
        # Where the time went. Main process: decompress+read; workers: parse and Parquet encode.
        main_decompress_read_s=round(read_s, 2),
        main_waiting_on_workers_s=round(wait_s, 2),
        worker_parse_s_total=round(totals["parse_s"], 2),
        worker_write_s_total=round(totals["write_s"], 2),
        bottleneck_hint=_hint(read_s, wait_s, elapsed),
        fallback_chunks=totals["fallback_chunks"],
        parity_check=parity,
        unknown_tags=dict(unknown.most_common(20)),
    )
    log("parse complete", games=f"{totals['games']:,}", wall=human_secs(elapsed),
        games_per_s=f"{stats['games_per_s']:,}" if stats["games_per_s"] else "?",
        bottleneck=stats["bottleneck_hint"])


def _hint(read_s: float, wait_s: float, wall: float) -> str:
    if wall <= 0:
        return "n/a"
    if wait_s / wall > 0.5:
        return "workers (parse/write) — add cores or a faster parser"
    if read_s / wall > 0.5:
        return "decompression/input — single zstd stream or download speed"
    return "balanced"


def cmd_commit(args, stats: dict) -> None:
    import pyarrow.parquet as pq
    from pyiceberg.catalog.sql import SqlCatalog

    parquet_dir = Path(args.parquet_dir)
    found = sorted(parquet_dir.glob("*.parquet"))
    if not found:
        raise FileNotFoundError(f"no parquet files in {args.parquet_dir}")
    manifest_path = parquet_dir / MANIFEST
    expected_rows = None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        listed = {f["file"] for f in manifest["files"]}
        on_disk = {p.name for p in found}
        if listed != on_disk:
            raise ValueError(f"{MANIFEST} lists {len(listed)} files but {len(on_disk)} are on disk "
                             f"(missing: {sorted(listed - on_disk)[:5]}, extra: {sorted(on_disk - listed)[:5]})")
        expected_rows = manifest["games"]
    else:
        log(f"no {MANIFEST} found; committing all parquet files without verification", "WARN")

    rows = sum(pq.ParquetFile(p).metadata.num_rows for p in found)
    if expected_rows is not None and rows != expected_rows:
        raise ValueError(f"parquet files hold {rows} rows but the manifest says {expected_rows}")
    stats.update(files=len(found), rows=rows)

    wh = Path(args.warehouse).resolve()
    wh.mkdir(parents=True, exist_ok=True)
    # fsspec FileIO: the default PyArrow FileIO mangles file:///C:/ paths on Windows.
    catalog = SqlCatalog("spike", uri=f"sqlite:///{(wh / 'catalog.db').as_posix()}", warehouse=wh.as_uri(),
                         **{"py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO"})
    catalog.create_namespace_if_not_exists("raw")
    ident = "raw.games_spike"
    if catalog.table_exists(ident):
        catalog.drop_table(ident)
    table = catalog.create_table(ident, schema=pq.read_schema(found[0]))
    log("commit started", files=len(found), rows=f"{rows:,}")
    t0 = time.perf_counter()
    table.add_files([p.resolve().as_uri() for p in found])
    elapsed = time.perf_counter() - t0
    snapshot = table.refresh().current_snapshot()
    added = int(snapshot.summary.get("added-records")) if snapshot else None
    if added != rows:
        raise ValueError(f"Iceberg snapshot added {added} records, expected {rows}")
    stats.update(seconds=round(elapsed, 2), snapshot_id=snapshot.snapshot_id,
                 snapshot_added_records=added)
    log("commit complete", seconds=round(elapsed, 2), snapshot=snapshot.snapshot_id)


def cmd_pychess(args, stats: dict) -> None:
    try:
        import chess.pgn
    except ImportError:
        raise SystemExit("python-chess is not installed: pip install chess") from None

    text = io.TextIOWrapper(open_text_stream(args.src), encoding="utf-8", errors="replace")
    log("pychess baseline started", games=args.games)
    progress = Progress("pychess", None, every=args.log_every)
    t0 = time.perf_counter()
    n = 0
    while n < args.games and chess.pgn.read_game(text) is not None:
        n += 1  # read_game parses headers AND replays every move
        stats["games"] = n
        progress.update(n)
    elapsed = time.perf_counter() - t0
    stats.update(games=n, seconds=round(elapsed, 2), games_per_s=round(n / elapsed) if elapsed else None)
    log("pychess baseline complete", games=n, games_per_s=stats["games_per_s"])


# --------------------------------------------------------------------------- entry point

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pgn-timing", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--report", help="append the JSON result to this file (one line per run)")
    p.add_argument("--log-every", type=float, default=10.0, help="seconds between progress lines")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download", help="resumable, retried, verified download of a .pgn.zst")
    d.add_argument("url")
    d.add_argument("--out", required=True)
    d.add_argument("--retries", type=int, default=5)
    d.add_argument("--no-verify", action="store_true", help="skip sha256 verification")
    d.add_argument("--checksums-url", help="sha256 list to verify against (default: sha256sums.txt next to the file)")
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
    pa.add_argument("--overwrite", action="store_true", help="replace output from an earlier run in --out")
    pa.add_argument("--compression", default="zstd")
    pa.add_argument("--limit-mb", type=int, help="stop after this many decompressed MB")
    pa.add_argument("--no-parity-check", action="store_true",
                    help="skip re-parsing the first chunk with the reference parser")
    pa.set_defaults(fn=cmd_parse)

    c = sub.add_parser("commit", help="register Parquet files in a local Iceberg table")
    c.add_argument("parquet_dir")
    c.add_argument("--warehouse", required=True, help="local directory for the Iceberg warehouse")
    c.set_defaults(fn=cmd_commit)

    pc = sub.add_parser("pychess", help="python-chess baseline on a sample of games")
    pc.add_argument("src")
    pc.add_argument("--games", type=int, default=20_000)
    pc.set_defaults(fn=cmd_pychess)
    return p


def main(argv=None) -> dict:
    args = build_parser().parse_args(argv)
    stats: dict = {"stage": args.cmd}
    status, code, error = "ok", 0, None
    t0 = time.perf_counter()
    try:
        args.fn(args, stats)
    except KeyboardInterrupt:
        status, code, error = "interrupted", 130, "KeyboardInterrupt"
        log("interrupted", "WARN")
    except SystemExit:
        raise
    except Exception as exc:  # record the failure, then exit non-zero
        status, code, error = "failed", 1, f"{type(exc).__name__}: {exc}"
        log(f"{args.cmd} failed: {error}", "ERROR")
        traceback.print_exc(file=sys.stderr)
    wall = time.perf_counter() - t0
    worker_usage = stats.pop("_worker_usage", None)
    result = {"status": status, **stats, "error": error, "total_s": round(wall, 2),
              "resources": resource_usage(wall, worker_usage), "env": _env(),
              "args": {k: v for k, v in vars(args).items() if k not in ("fn",)},
              "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if error is None:
        result.pop("error")
    print(json.dumps(result, indent=2, default=str))
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
        with open(args.report, "a") as fh:
            fh.write(json.dumps(result, default=str) + "\n")
    if code:
        raise SystemExit(code)
    return result


def entrypoint() -> int:
    """Console-script entry point: exit status 0 on success (main() raises SystemExit otherwise)."""
    main()
    return 0


if __name__ == "__main__":
    entrypoint()
