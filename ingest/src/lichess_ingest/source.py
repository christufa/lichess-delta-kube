"""Lichess inventory (what months exist) and verified, resumable downloads."""
from __future__ import annotations

import hashlib
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import zstandard

from .log import Progress, human_bytes, log

DEFAULT_BASE_URL = "https://database.lichess.org/standard/"
USER_AGENT = "lichess-ingest/0.1 (+https://github.com/christufa/lichess-delta-kube)"
MAX_WINDOW = 1 << 31  # Lichess dumps use long-distance matching windows
TIMEOUT_S = 60


@dataclass(frozen=True)
class MonthEntry:
    month: str      # "YYYY-MM"
    file: str       # lichess_db_standard_rated_YYYY-MM.pgn.zst
    url: str
    games: int      # from counts.txt
    sha256: str     # from sha256sums.txt


def _get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=TIMEOUT_S)


def _get_text(url: str) -> str:
    with _get(url) as resp:
        return resp.read().decode("utf-8", errors="replace")


def month_of(filename: str) -> str:
    # lichess_db_standard_rated_2025-09.pgn.zst -> 2025-09
    return filename.rsplit("_", 1)[-1].split(".", 1)[0]


def fetch_inventory(base_url: str = DEFAULT_BASE_URL) -> list[MonthEntry]:
    """All months listed by Lichess, oldest first. Uses list.txt (not the directory listing)."""
    base = base_url.rstrip("/") + "/"
    urls = [u.strip() for u in _get_text(base + "list.txt").splitlines() if u.strip()]
    counts, sums = {}, {}
    for line in _get_text(base + "counts.txt").splitlines():
        parts = line.split()
        if len(parts) == 2:
            counts[parts[0]] = int(parts[1])
    for line in _get_text(base + "sha256sums.txt").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            sums[parts[-1].lstrip("*")] = parts[0].lower()
    entries = []
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        if not name.endswith(".pgn.zst"):
            continue
        if name not in counts or name not in sums:
            raise ValueError(f"{name} is in list.txt but missing from counts.txt or sha256sums.txt")
        entries.append(MonthEntry(month_of(name), name, url, counts[name], sums[name]))
    return sorted(entries, key=lambda e: e.month)


def select_months(entries: list[MonthEntry], spec: str | None) -> list[MonthEntry]:
    """spec: None (all), 'YYYY-MM', 'YYYY-MM:YYYY-MM' (inclusive), or a comma-separated mix."""
    if not spec:
        return list(entries)
    wanted = []
    for part in spec.split(","):
        part = part.strip()
        if ":" in part:
            lo, hi = (p.strip() for p in part.split(":", 1))
            wanted += [e for e in entries if lo <= e.month <= hi]
        else:
            match = [e for e in entries if e.month == part or e.month.startswith(part + "-")]
            if not match:
                raise ValueError(f"no Lichess month matches {part!r}")
            wanted += match
    seen, out = set(), []
    for e in sorted(wanted, key=lambda e: e.month):
        if e.month not in seen:
            seen.add(e.month)
            out.append(e)
    return out


# --------------------------------------------------------------------------- download

def sha256_file(path: str, block: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(block):
            h.update(chunk)
    return h.hexdigest()


def _attempt(url: str, out: str, stats: dict, progress_every: float) -> None:
    start = os.path.getsize(out) if os.path.exists(out) else 0
    try:
        resp = _get(url, {"Range": f"bytes={start}-"} if start else None)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and start:  # we already have the whole file
            return
        raise
    with resp:
        if start and resp.status != 206:
            log("server ignored the Range request; restarting from zero", "WARN")
            start = 0
        total = (resp.length + start) if resp.length is not None else None
        progress = Progress(f"download {os.path.basename(out)}", total, every=progress_every)
        done = start
        with open(out, "ab" if start else "wb") as fh:
            while data := resp.read(8 << 20):
                fh.write(data)
                done += len(data)
                stats["downloaded_bytes"] = stats.get("downloaded_bytes", 0) + len(data)
                progress.update(done)
    if total is not None and os.path.getsize(out) < total:
        raise ConnectionError(f"connection closed early at {os.path.getsize(out)} of {total} bytes")


def download_verified(entry: MonthEntry, work_dir: str, retries: int = 5, progress_every: float = 30.0) -> tuple[str, dict]:
    """Download (resuming any partial file) and verify sha256. Returns (path, stats)."""
    src_dir = os.path.join(work_dir, "src")
    os.makedirs(src_dir, exist_ok=True)
    out = os.path.join(src_dir, entry.file)
    stats: dict = {"resumed_from_bytes": os.path.getsize(out) if os.path.exists(out) else 0}
    t0 = time.perf_counter()
    for attempt in range(1, retries + 2):
        try:
            _attempt(entry.url, out, stats, progress_every)
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429:
                raise
            if attempt > retries:
                raise
            wait = min(60, 2 ** attempt)
            log(f"{entry.month}: download attempt {attempt} failed; retrying in {wait}s", "WARN", error=repr(exc))
            stats["retries"] = attempt
            time.sleep(wait)
    elapsed = time.perf_counter() - t0
    actual = sha256_file(out)
    if actual != entry.sha256:
        os.replace(out, out + ".bad")  # don't resume from corrupt bytes next time
        raise ValueError(f"{entry.month}: sha256 mismatch (expected {entry.sha256}, got {actual}); "
                         f"moved the file aside, it will be downloaded again")
    size = os.path.getsize(out)
    stats.update(compressed_bytes=size, download_s=round(elapsed, 2),
                 download_mb_per_s=round(stats.get("downloaded_bytes", 0) / elapsed / 1e6, 1) if elapsed else None)
    log(f"{entry.month}: downloaded and verified", size=human_bytes(size), seconds=round(elapsed, 1))
    return out, stats


def open_decompressed(path: str):
    """Return (compressed_file, decompressed_reader); the file's .tell() gives progress."""
    fh = open(path, "rb")
    return fh, zstandard.ZstdDecompressor(max_window_size=MAX_WINDOW).stream_reader(fh, read_size=4 << 20)
