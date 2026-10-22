"""Lichess inventory (what months exist) and verified, resumable downloads.

Downloads use BitTorrent when aria2c is available (Lichess publishes a .torrent for
every month and asks bulk downloaders to use them; many peers beat one throttled
HTTP connection), and fall back to a resumable HTTPS download otherwise. Either way
the file is checked against Lichess's sha256sums.txt before it is used.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
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


RETRY_STATUS = (429, 502, 503, 504)


def _get_text(url: str, attempts: int = 6, backoff_s: float = 10.0) -> str:
    # Many pods starting at once (a backfill of small months) each fetch the inventory, and
    # Lichess answers some of them 429: wait (Retry-After if given) and try again.
    for attempt in range(attempts):
        try:
            with _get(url) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRY_STATUS or attempt == attempts - 1:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff_s * 2 ** attempt
            log(f"{url}: HTTP {exc.code}, retrying in {wait:.0f} s", "WARN")
            time.sleep(wait)
    raise AssertionError("unreachable")


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
    """spec: None (all), 'YYYY', 'YYYY-MM', a range of either ('2016:2019', inclusive), or a comma-separated mix."""
    if not spec:
        return list(entries)
    wanted = []
    for part in spec.split(","):
        part = part.strip()
        if ":" in part:
            lo, hi = (p.strip() for p in part.split(":", 1))
            # A bare year covers its whole year: '2016:2019' ends with 2019-12, not before 2019-01.
            lo, hi = (lo + "-01" if len(lo) == 4 else lo), (hi + "-12" if len(hi) == 4 else hi)
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


DOWNLOAD_METHODS = ("auto", "torrent", "http")
TORRENT_STALL_S = 300  # give up on the swarm after this long without download progress


def resolve_method(method: str) -> str:
    if method == "auto":
        return "torrent" if shutil.which("aria2c") else "http"
    return method


def _torrent(entry: MonthEntry, src_dir: str, out: str, stats: dict, progress_every: float) -> None:
    """Download via the month's .torrent with aria2c; raise if it fails or stalls."""
    aria2c = shutil.which("aria2c")
    if not aria2c:
        raise FileNotFoundError("aria2c not found on PATH")
    cmd = [
        aria2c, entry.url + ".torrent",
        "--dir", src_dir,
        "--follow-torrent=mem", "--seed-time=0",          # leech only; peers still get pieces while we download
        "--continue=true", "--check-integrity=true",       # resume and verify pieces already on disk
        "--file-allocation=none", "--auto-file-renaming=false", "--allow-overwrite=true",
        f"--bt-stop-timeout={TORRENT_STALL_S}",           # no progress for this long -> exit non-zero
        "--bt-max-peers=80", "--max-connection-per-server=4",
        "--show-console-readout=false", f"--summary-interval={max(1, int(progress_every))}",
        "--console-log-level=warn", "--download-result=hide", "--enable-color=false",
        f"--user-agent={USER_AGENT}",
    ]
    log(f"{entry.month}: torrent download", torrent=entry.url + ".torrent")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    tail: list[str] = []
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        tail = (tail + [line])[-5:]
        if line.startswith("[#"):          # summary: [#gid 1.2GiB/29GiB(4%) CN:44 SD:30 DL:25MiB ETA:18m]
            log(f"download {entry.file}", aria2=line.strip("[]"))
        elif "ERROR" in line or "WARN" in line or "Exception" in line:
            log(f"{entry.month}: aria2c", "WARN", line=line)
    code = proc.wait()
    if code != 0 or not os.path.exists(out) or os.path.exists(out + ".aria2"):
        raise RuntimeError(f"aria2c exited with {code}: {' | '.join(tail)}")
    stats["downloaded_bytes"] = os.path.getsize(out) - stats.get("resumed_from_bytes", 0)


def _discard_partial_torrent(out: str) -> None:
    """A half-done torrent file has holes, so it can't be resumed over HTTP."""
    if os.path.exists(out + ".aria2"):
        for path in (out, out + ".aria2"):
            if os.path.exists(path):
                os.remove(path)


def download_verified(entry: MonthEntry, work_dir: str, retries: int = 5, progress_every: float = 30.0,
                      method: str = "auto") -> tuple[str, dict]:
    """Download (resuming any partial file) and verify sha256. Returns (path, stats).

    method: "torrent" (aria2c), "http", or "auto" (torrent if aria2c is installed).
    A torrent that fails, stalls or gives a file with the wrong sha256 falls back to HTTPS
    (some months were republished without a new .torrent: 2018-03's is the old file).
    """
    src_dir = os.path.join(work_dir, "src")
    os.makedirs(src_dir, exist_ok=True)
    out = os.path.join(src_dir, entry.file)
    stats: dict = {"resumed_from_bytes": os.path.getsize(out) if os.path.exists(out) else 0}
    t0 = time.perf_counter()
    method = resolve_method(method)
    stats["method"] = method
    actual = None
    if method == "torrent":
        try:
            _torrent(entry, src_dir, out, stats, progress_every)
            actual = sha256_file(out)
            if actual != entry.sha256:
                os.replace(out, out + ".bad")
                raise RuntimeError("the torrent's file doesn't match sha256sums.txt")
        except (OSError, RuntimeError) as exc:
            log(f"{entry.month}: torrent failed; falling back to HTTPS", "WARN", error=str(exc)[:300])
            stats.update(method="http", torrent_error=str(exc)[:300])
            _discard_partial_torrent(out)
            stats["resumed_from_bytes"] = os.path.getsize(out) if os.path.exists(out) else 0
    for attempt in range(1, retries + 2) if stats["method"] == "http" else ():
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
    if stats["method"] == "http":
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
