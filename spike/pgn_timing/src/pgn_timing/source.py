"""Open a .pgn.zst from a local path or an HTTP(S) URL, and download dumps robustly."""
from __future__ import annotations

import hashlib
import os
import time
import urllib.error
import urllib.request

import zstandard

from .log import Progress, human_bytes, log

USER_AGENT = "lichess-data-platform-spike/0.2"
# Lichess dumps can use long-distance matching windows; allow up to 2 GiB.
MAX_WINDOW = 1 << 31
TIMEOUT_S = 60


class CountingReader:
    """Wraps a binary stream, counts bytes read through it and knows the total size if available."""

    def __init__(self, raw, total: int | None = None):
        self.raw, self.total, self.bytes = raw, total, 0

    def read(self, n=-1):
        data = self.raw.read(n)
        self.bytes += len(data)
        return data

    def readable(self):
        return True

    def close(self):
        self.raw.close()


def is_url(src: str) -> bool:
    return src.startswith(("http://", "https://"))


def _request(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return urllib.request.urlopen(req, timeout=TIMEOUT_S)


def open_compressed(src: str) -> CountingReader:
    if is_url(src):
        resp = _request(src)
        return CountingReader(resp, resp.length)
    return CountingReader(open(src, "rb"), os.path.getsize(src))


class LimitedReader:
    """Stops after exactly `limit` bytes; `truncated` tells whether the limit was hit."""

    def __init__(self, raw, limit: int | None):
        self.raw, self.limit, self.bytes = raw, limit, 0

    def read(self, n=-1):
        if self.limit is not None and self.bytes >= self.limit:
            return b""
        if self.limit is not None:
            remaining = self.limit - self.bytes
            n = remaining if n is None or n < 0 else min(n, remaining)
        data = self.raw.read(n)
        self.bytes += len(data)
        return data

    @property
    def truncated(self) -> bool:
        return self.limit is not None and self.bytes >= self.limit


def open_decompressed(src: str, limit_bytes: int | None = None):
    """Return (compressed_counter, decompressed_reader)."""
    compressed = open_compressed(src)
    stream = zstandard.ZstdDecompressor(max_window_size=MAX_WINDOW).stream_reader(compressed, read_size=4 << 20)
    return compressed, LimitedReader(stream, limit_bytes)


def open_text_stream(src: str):
    """A decompressed stream suitable for io.TextIOWrapper (used by the python-chess baseline)."""
    compressed = open_compressed(src)
    return zstandard.ZstdDecompressor(max_window_size=MAX_WINDOW).stream_reader(compressed, read_size=4 << 20)


# --------------------------------------------------------------------------- download

def sha256_file(path: str, block: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(block):
            h.update(chunk)
    return h.hexdigest()


def fetch_expected_sha256(checksums_url: str, filename: str) -> str | None:
    """Look up `filename` in a sha256sum-style list (`<hex>  <name>` per line)."""
    with _request(checksums_url) as resp:
        for line in resp.read().decode("utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
                return parts[0].lower()
    return None


def default_checksums_url(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/sha256sums.txt"


def _download_attempt(url: str, out: str, stats: dict, block: int, progress_every: float) -> None:
    start = os.path.getsize(out) if os.path.exists(out) else 0
    headers = {"Range": f"bytes={start}-"} if start else {}
    try:
        resp = _request(url, headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and start:  # range not satisfiable: we already have the whole file
            stats.setdefault("total_bytes", start)
            return
        raise
    with resp:
        if start and resp.status != 206:
            log("server ignored the Range request; restarting from zero", "WARN")
            start = 0
        total = (resp.length + start) if resp.length is not None else None
        if total:
            stats["total_bytes"] = total
        progress = Progress("download", total, every=progress_every)
        done = start
        with open(out, "ab" if start else "wb") as fh:
            while True:
                data = resp.read(block)
                if not data:
                    break
                fh.write(data)
                done += len(data)
                stats["downloaded_bytes"] = stats.get("downloaded_bytes", 0) + len(data)
                progress.update(done)
    if total is not None and os.path.getsize(out) < total:
        raise ConnectionError(f"connection closed early at {os.path.getsize(out)} of {total} bytes")


def download(url: str, out: str, stats: dict, retries: int = 5, verify: bool = True,
             checksums_url: str | None = None, block: int = 8 << 20, progress_every: float = 10.0) -> dict:
    """Resumable download with retries, then optional sha256 verification.

    Re-running after an interruption resumes from the bytes already on disk.
    """
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    stats["resumed_from_bytes"] = os.path.getsize(out) if os.path.exists(out) else 0
    if stats["resumed_from_bytes"]:
        log("resuming download", have=human_bytes(stats["resumed_from_bytes"]))
    t0 = time.perf_counter()
    for attempt in range(1, retries + 2):
        try:
            _download_attempt(url, out, stats, block, progress_every)
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429:
                raise  # 4xx (except rate limiting) will not fix itself
            if attempt > retries:
                raise
            wait = min(60, 2 ** attempt)
            log(f"download attempt {attempt} failed; retrying in {wait}s", "WARN", error=repr(exc))
            stats["retries"] = attempt
            time.sleep(wait)
    elapsed = time.perf_counter() - t0
    size = os.path.getsize(out)
    stats.update(total_bytes=size, download_s=round(elapsed, 2),
                 mb_per_s=round(stats.get("downloaded_bytes", 0) / elapsed / 1e6, 1) if elapsed else None)
    log("download complete", size=human_bytes(size), seconds=round(elapsed, 1))

    if verify:
        filename = url.rsplit("/", 1)[-1]
        source = checksums_url or default_checksums_url(url)
        try:
            expected = fetch_expected_sha256(source, filename)
        except (urllib.error.URLError, OSError) as exc:
            expected = None
            log("could not fetch checksums; skipping verification", "WARN", url=source, error=repr(exc))
        t1 = time.perf_counter()
        actual = sha256_file(out)
        stats.update(sha256=actual, verify_s=round(time.perf_counter() - t1, 2))
        if expected is None:
            stats["checksum"] = "unavailable"
        elif expected == actual:
            stats["checksum"] = "ok"
            log("checksum ok", sha256=actual[:16] + "...")
        else:
            stats["checksum"] = "mismatch"
            stats["expected_sha256"] = expected
            raise ValueError(f"sha256 mismatch for {out}: expected {expected}, got {actual}. "
                             f"Delete the file and download again.")
    return stats
