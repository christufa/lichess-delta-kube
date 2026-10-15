"""lichess-ingest: bootstrap Lichess standard games into raw Parquet in ADLS, month by month.

  plan   show which months are done, pending or changed upstream
  run    download → verify → parse → upload → manifest, oldest month first;
         re-running skips months already done, so it can be stopped and resumed

Progress goes to stderr; each finished month is also appended to <work>/runs.jsonl.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from .dest import BlobDest, open_dest
from .log import human_bytes, human_secs, log
from .month import process_month, state_name
from .source import DEFAULT_BASE_URL, download_verified, fetch_inventory, select_months


def _status(entry, dest) -> tuple[str, dict | None]:
    state = dest.get_json(state_name(entry.month))
    if state is None:
        return "pending", None
    if state.get("sha256") != entry.sha256:
        return "changed", state  # Lichess republished this month
    return "done", state


def cmd_plan(args) -> int:
    dest = open_dest(args.dest)
    entries = select_months(fetch_inventory(args.base_url), args.months)
    rows = [(e, *_status(e, dest)) for e in entries]
    todo = [e for e, st, _ in rows if st != "done"]
    print(f"{'month':8} {'status':8} {'games':>13} {'parquet':>10}")
    for e, st, state in rows:
        size = human_bytes(state["parquet_bytes"]) if state else ""
        print(f"{e.month:8} {st:8} {e.games:>13,} {size:>10}")
    done_bytes = sum(s["parquet_bytes"] for _, st, s in rows if st == "done")
    print(f"\n{len(rows) - len(todo)} done ({human_bytes(done_bytes)} Parquet), {len(todo)} to do "
          f"({sum(e.games for e in todo):,} games)", file=sys.stderr)
    return 0


def cmd_run(args) -> int:
    dest = open_dest(args.dest)
    if isinstance(dest, BlobDest):
        dest.check()
        log("destination ok", url=dest.url, auth=dest.auth)
    entries = select_months(fetch_inventory(args.base_url), args.months)
    todo = []
    for e in entries:
        st, _ = _status(e, dest)
        if st == "done" and not args.force:
            continue
        if st == "changed":
            log(f"{e.month}: checksum changed upstream; re-ingesting", "WARN")
        todo.append(e)
    if args.max_months:
        todo = todo[: args.max_months]
    if not todo:
        log("nothing to do: all selected months are done")
        return 0
    log("starting", months=len(todo), first=todo[0].month, last=todo[-1].month,
        games=f"{sum(e.games for e in todo):,}", workers=args.workers, prefetch=args.prefetch)

    os.makedirs(args.work, exist_ok=True)
    report = os.path.join(args.work, "runs.jsonl")
    run_id = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    failures = 0
    t_start = time.perf_counter()

    # Download ahead of parsing so the network and CPUs stay busy at the same time.
    downloader = ThreadPoolExecutor(max_workers=1)
    pending = {}

    def schedule(i):
        if i < len(todo) and i not in pending:
            pending[i] = downloader.submit(download_verified, todo[i], args.work, args.retries, args.log_every)

    for i in range(1 + args.prefetch):
        schedule(i)
    try:
        for i, entry in enumerate(todo):
            record = {"month": entry.month, "run_id": run_id, "games_expected": entry.games}
            src_path = None
            try:
                src_path, dl = pending.pop(i).result()
                schedule(i + 1 + args.prefetch)
                record["download"] = dl
                manifest = process_month(
                    entry, src_path, dest, work_dir=args.work, run_id=run_id, workers=args.workers,
                    chunk_mb=args.chunk_mb, file_mb=args.file_mb, compression="zstd", level=args.zstd_level,
                    upload_concurrency=args.upload_concurrency, check_parity=not args.no_parity_check,
                    progress_every=args.log_every)
                record.update(status="ok", games=manifest["games"], files=len(manifest["files"]),
                              parquet_bytes=manifest["parquet_bytes"], stats=manifest["stats"])
                if not args.keep_source:
                    os.remove(src_path)
            except KeyboardInterrupt:
                record.update(status="interrupted")
                raise
            except Exception as exc:
                failures += 1
                record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
                log(f"{entry.month} failed: {record['error']}", "ERROR")
                traceback.print_exc(file=sys.stderr)
                schedule(i + 1 + args.prefetch)
                if not args.keep_going:
                    raise SystemExit(1)
            finally:
                record["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                with open(report, "a") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
            elapsed = time.perf_counter() - t_start
            left = len(todo) - i - 1
            log(f"progress: {i + 1}/{len(todo)} months", elapsed=human_secs(elapsed),
                eta=human_secs(elapsed / (i + 1) * left) if left else "0m00s")
    except KeyboardInterrupt:
        log("interrupted; finished months are kept, re-run to continue", "WARN")
        downloader.shutdown(wait=False, cancel_futures=True)
        return 130
    downloader.shutdown(wait=True)
    log("finished", months=len(todo), failed=failures, elapsed=human_secs(time.perf_counter() - t_start))
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lichess-ingest", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dest", default=os.environ.get("LICHESS_DEST"),
                   help="output location (env LICHESS_DEST): abfss://<container>@<account>.dfs.core.windows.net[/prefix], "
                        "https://<account>.blob.core.windows.net/<container>[/prefix] or a local directory")
    p.add_argument("--base-url", default=os.environ.get("LICHESS_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--months", help="'2013-01:2016-12', '2017', '2025-09' or a comma-separated mix (default: all)")
    p.add_argument("--log-every", type=float, default=30.0, help="seconds between progress lines")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("plan", help="show done / pending months")
    pl.set_defaults(fn=cmd_plan)

    r = sub.add_parser("run", help="ingest pending months, oldest first")
    r.add_argument("--work", default=os.environ.get("LICHESS_WORK", "./work"),
                   help="local scratch for downloads and staging (needs ~2-3 months of space)")
    r.add_argument("--max-months", type=int, help="stop after this many months")
    r.add_argument("--workers", type=int, default=6)
    r.add_argument("--chunk-mb", type=float, default=128, help="decompressed MB per parse chunk (= one row group)")
    r.add_argument("--file-mb", type=float, default=512, help="roll to a new Parquet file at this size")
    r.add_argument("--zstd-level", type=int, default=9)
    r.add_argument("--upload-concurrency", type=int, default=4, help="files uploaded in parallel")
    r.add_argument("--prefetch", type=int, default=1, help="months to download ahead of parsing")
    r.add_argument("--retries", type=int, default=5)
    r.add_argument("--keep-source", action="store_true", help="keep downloaded .pgn.zst files")
    r.add_argument("--keep-going", action="store_true", help="continue with the next month after a failure")
    r.add_argument("--force", action="store_true", help="re-ingest months that are already done")
    r.add_argument("--no-parity-check", action="store_true")
    r.set_defaults(fn=cmd_run)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.dest:
        raise SystemExit("--dest (or LICHESS_DEST) is required")
    return args.fn(args)


def entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
