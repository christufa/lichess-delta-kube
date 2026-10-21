"""Daily spend per user, in SQLite on a persistent volume, so the cap survives restarts.

One replica owns the file (the chart runs one pod with a ReadWriteOnce disk): a cap held in
memory would reset on every restart, and two replicas would each allow the full amount.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from pathlib import Path


def today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


class Ledger:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("CREATE TABLE IF NOT EXISTS spend (day TEXT NOT NULL, oid TEXT NOT NULL, "
                             "usd REAL NOT NULL, requests INTEGER NOT NULL, PRIMARY KEY (day, oid))")

    def add(self, oid: str, usd: float, day: str | None = None) -> None:
        day = day or today()
        with self._lock:
            self._db.execute("INSERT INTO spend (day, oid, usd, requests) VALUES (?, ?, ?, 1) "
                             "ON CONFLICT (day, oid) DO UPDATE SET usd = usd + excluded.usd, requests = requests + 1",
                             (day, oid, usd))

    def spent(self, oid: str | None = None, day: str | None = None) -> float:
        """Today's spend for one user, or for everyone with oid=None."""
        day = day or today()
        with self._lock:
            if oid is None:
                row = self._db.execute("SELECT coalesce(sum(usd), 0) FROM spend WHERE day = ?", (day,)).fetchone()
            else:
                row = self._db.execute("SELECT coalesce(sum(usd), 0) FROM spend WHERE day = ? AND oid = ?",
                                       (day, oid)).fetchone()
        return float(row[0])
