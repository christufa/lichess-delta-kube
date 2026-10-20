"""Proves what chess-api may read in Trino, from inside a running pod:

    kubectl exec -n chess-api deploy/chess-api -- python -m chess_api.access_check

Expected: signed in as chess-api, lake.gold readable, lake.curated and lake.raw denied.
Exits 1 if any of that doesn't hold.
"""
from __future__ import annotations

import sys

from .settings import Settings
from .trino_client import QueryRunner, TrinoRunner, TrinoUnavailable

ALLOWED = ["lake.gold.monthly_summary", "lake.gold.openings", "lake.gold.rating_distribution"]
DENIED = ["lake.curated.games", "lake.curated.player_games", "lake.raw.games"]


def run(db: QueryRunner, out=print) -> bool:
    ok = True
    who = db.query("SELECT current_user AS who")[0]["who"]
    out(f"signed in as: {who}")
    ok &= who == "chess-api"
    for table in ALLOWED:
        try:
            n = db.query(f"SELECT count(*) AS n FROM {table}")[0]["n"]  # noqa: S608 (constants)
            out(f"PASS  read {table}: {n} rows")
        except TrinoUnavailable as e:
            out(f"FAIL  read {table}: {e}")
            ok = False
    for table in DENIED:
        try:
            db.query(f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608 (constants)
            out(f"FAIL  {table} is readable; it must not be")
            ok = False
        except TrinoUnavailable as e:
            # Trino says "Access Denied", or hides the table entirely ("does not exist").
            denied = "Access Denied" in str(e) or "does not exist" in str(e)
            out(f"{'PASS' if denied else 'FAIL'}  {table} denied: {str(e)[:160]}")
            ok &= denied
    out("OK" if ok else "NOT OK")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run(TrinoRunner(Settings())) else 1)
