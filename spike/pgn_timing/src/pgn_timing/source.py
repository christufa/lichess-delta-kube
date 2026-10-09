"""Open a .pgn.zst from a local path or an HTTP(S) URL as a decompressed stream."""
from __future__ import annotations

import os
import time
import urllib.request

import zstandard

USER_AGENT = "lichess-data-platform-spike/0.1"
# Lichess dumps can use long-distance matching windows; allow up to 2 GiB.
MAX_WINDOW = 1 << 31


class CountingReader:
    """Wraps a binary stream and counts bytes read through it."""

    def __init__(self, raw):
        self.raw = raw
        self.bytes = 0

    def read(self, n=-1):
        data = self.raw.read(n)
        self.bytes += len(data)
        return data

    def close(self):
        self.raw.close()


def is_url(src: str) -> bool:
    return src.startswith(("http://", "https://"))


def open_compressed(src: str) -> CountingReader:
    if is_url(src):
        request = urllib.request.Request(src, headers={"User-Agent": USER_AGENT})
        return CountingReader(urllib.request.urlopen(request, timeout=60))
    return CountingReader(open(src, "rb"))


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
    dctx = zstandard.ZstdDecompressor(max_window_size=MAX_WINDOW)
    stream = dctx.stream_reader(compressed, read_size=4 << 20)
    return compressed, LimitedReader(stream, limit_bytes)


def download(url: str, out: str, block: int = 8 << 20, progress_every: float = 10.0) -> dict:
    """Resumable download to `out` using HTTP Range requests."""
    start = os.path.getsize(out) if os.path.exists(out) else 0
    headers = {"User-Agent": USER_AGENT}
    if start:
        headers["Range"] = f"bytes={start}-"
    request = urllib.request.Request(url, headers=headers)
    t0 = last = time.perf_counter()
    got = 0
    with urllib.request.urlopen(request, timeout=60) as resp:
        if start and resp.status != 206:
            start = 0  # server ignored the range; restart
        total = resp.length + start if resp.length else None
        with open(out, "ab" if start else "wb") as fh:
            while True:
                data = resp.read(block)
                if not data:
                    break
                fh.write(data)
                got += len(data)
                now = time.perf_counter()
                if now - last >= progress_every:
                    last = now
                    done = start + got
                    pct = f" ({100 * done / total:.1f}%)" if total else ""
                    print(f"  {done / 1e9:.2f} GB{pct} at {got / (now - t0) / 1e6:.1f} MB/s", flush=True)
    elapsed = time.perf_counter() - t0
    return {"stage": "download", "url": url, "out": out, "resumed_from_bytes": start,
            "downloaded_bytes": got, "total_bytes": start + got, "seconds": round(elapsed, 2),
            "mb_per_s": round(got / elapsed / 1e6, 1) if elapsed else None}
