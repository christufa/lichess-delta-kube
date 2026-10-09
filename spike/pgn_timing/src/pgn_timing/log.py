"""Timestamped stderr logging, throttled progress with ETA, and resource usage.

stdout is reserved for the final JSON result; everything human-facing goes to stderr.
"""
from __future__ import annotations

import sys
import time

try:  # not available on Windows
    import resource
except ImportError:  # pragma: no cover - platform dependent
    resource = None


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg: str, level: str = "INFO", **fields) -> None:
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"{_ts()} {level:5s} {msg}{' ' + extra if extra else ''}", file=sys.stderr, flush=True)


def human_bytes(n: float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000
    return f"{n:.1f} TB"


def human_secs(s: float | None) -> str:
    if s is None or s != s or s < 0:
        return "?"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m}m{sec:02d}s"


class Progress:
    """Logs at most every `every` seconds: done/total, %, rate and ETA."""

    def __init__(self, label: str, total: int | None, every: float = 10.0):
        self.label, self.total, self.every = label, total, every
        self.t0 = self.last = time.perf_counter()

    def update(self, done: int, force: bool = False, **fields) -> None:
        now = time.perf_counter()
        if not force and now - self.last < self.every:
            return
        self.last = now
        elapsed = now - self.t0
        rate = done / elapsed if elapsed > 0 else 0.0
        parts = {"done": human_bytes(done)}
        if self.total:
            pct = 100.0 * done / self.total
            parts["of"] = human_bytes(self.total)
            parts["pct"] = f"{pct:.1f}%"
            parts["eta"] = human_secs((self.total - done) / rate) if rate > 0 else "?"
        parts["rate"] = f"{human_bytes(rate)}/s"
        parts["elapsed"] = human_secs(elapsed)
        log(self.label, **parts, **fields)


def self_usage() -> dict | None:
    """This process's peak RSS (bytes) and CPU seconds so far; None where unsupported."""
    if resource is None:
        return None
    ru = resource.getrusage(resource.RUSAGE_SELF)
    scale = 1 if sys.platform == "darwin" else 1024
    return {"rss": ru.ru_maxrss * scale, "cpu": ru.ru_utime + ru.ru_stime}


def resource_usage(wall_s: float, workers: dict | None = None) -> dict:
    """Peak memory and CPU time for the main process and worker processes.

    `workers` maps worker pid -> latest self_usage() reported by that worker. Workers
    started via forkserver are not our children, so RUSAGE_CHILDREN would miss them.
    """
    if resource is None:
        return {"available": False}
    self_ru = resource.getrusage(resource.RUSAGE_SELF)
    # ru_maxrss is KiB on Linux, bytes on macOS
    scale = 1 if sys.platform == "darwin" else 1024
    cpu_self = self_ru.ru_utime + self_ru.ru_stime
    workers = workers or {}
    cpu_workers = sum(w["cpu"] for w in workers.values())
    peak_worker = max((w["rss"] for w in workers.values()), default=0)
    return {
        "available": True,
        "peak_rss_main_mb": round(self_ru.ru_maxrss * scale / 1e6, 1),
        "peak_rss_largest_worker_mb": round(peak_worker / 1e6, 1),
        "est_peak_rss_total_mb": round((self_ru.ru_maxrss * scale + peak_worker * len(workers)) / 1e6, 1),
        "worker_processes": len(workers),
        "cpu_s_main": round(cpu_self, 2),
        "cpu_s_workers": round(cpu_workers, 2),
        "avg_cores_busy": round((cpu_self + cpu_workers) / wall_s, 2) if wall_s > 0 else None,
    }
