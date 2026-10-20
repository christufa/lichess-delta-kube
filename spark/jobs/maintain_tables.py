"""Iceberg maintenance on every lake table: compact, rewrite manifests, expire snapshots,
remove orphan files (lichess_spark.maintenance). Weekly via the lake-maintenance CronWorkflow.

  spark-job job=maintain_tables args='[]'                                   # every table
  spark-job job=maintain_tables args='["--tables", "curated.player_games"]'
  spark-job job=maintain_tables args='["--orphans-dry-run"]'                # list orphans, delete none

Exits 1 if any table failed (the others still run).
"""
from __future__ import annotations

import argparse
import sys
import traceback
from dataclasses import replace

from lichess_spark import get_spark, maintenance


def main(argv=None) -> int:
    d = maintenance.DEFAULTS
    p = argparse.ArgumentParser()
    p.add_argument("--tables", help="comma-separated namespace.table; default: every table in "
                                    + ", ".join(maintenance.NAMESPACES))
    p.add_argument("--expire-days", type=int, default=d.expire_days)
    p.add_argument("--retain-last", type=int, default=d.retain_last)
    p.add_argument("--orphan-days", type=int, default=d.orphan_days)
    p.add_argument("--min-input-files", type=int, default=d.min_input_files)
    p.add_argument("--orphans-dry-run", action="store_true", help="list orphan files without deleting them")
    args = p.parse_args(argv)
    defaults = replace(d, expire_days=args.expire_days, retain_last=args.retain_last,
                       orphan_days=args.orphan_days, min_input_files=args.min_input_files)

    spark = get_spark("maintain-tables")
    tables = [t.strip() for t in args.tables.split(",")] if args.tables else maintenance.tables(spark)
    print(f"tables: {tables}")
    failed = []
    for table in tables:
        try:
            r = maintenance.maintain(spark, table, defaults, args.orphans_dry_run)
        except Exception:  # noqa: BLE001  (one table's failure shouldn't stop the rest)
            traceback.print_exc()
            failed.append(table)
            print(f"{table}: FAILED")
            continue
        b, a = r["before"], r["after"]
        orphans = f"{len(r['orphans'])} orphan files {'found (dry run)' if args.orphans_dry_run else 'removed'}"
        print(f"{table}: {r['settings'].rewrite}; files {b['files']} → {a['files']}, {b['gb']} → {a['gb']} GB, "
              f"snapshots {b['snapshots']} → {a['snapshots']}, manifests {b['manifests']} → {a['manifests']}; {orphans}")
        for path in r["orphans"][:20]:
            print(f"  orphan: {path}")
    print(f"{len(tables) - len(failed)} tables maintained, {len(failed)} failed {failed or ''}")
    spark.stop()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
