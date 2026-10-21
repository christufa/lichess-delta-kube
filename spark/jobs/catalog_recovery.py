"""Catalog disaster recovery (lichess_spark.catalog_recovery; docs/runbooks/catalog-recovery.md).

  spark-job job=catalog_recovery args='["snapshot"]'           # write the inventory (weekly, lake-maintenance)
  spark-job job=catalog_recovery args='["drill"]'              # read every table from its files alone, compare
  spark-job job=catalog_recovery args='["live-drill"]'         # the real thing, on a throwaway copy (ops.dr_drill)
  spark-job job=catalog_recovery args='["restore", "--dry-run"]'
  spark-job job=catalog_recovery args='["restore"]'            # register every inventory table the catalog lacks

--tables namespace.table,... limits drill and restore. Exits 1 if anything didn't check out.
"""
from __future__ import annotations

import argparse
import sys
import traceback

from lichess_spark import catalog_recovery as cr
from lichess_spark import dest_root, get_spark

DRILL_TABLE = "ops.dr_drill"
DRILL_SOURCE = "gold.rating_distribution"  # small: a few thousand rows per month


def selected(entries: list[cr.Entry], tables: str | None) -> list[cr.Entry]:
    if not tables:
        return entries
    wanted = {t.strip() for t in tables.split(",") if t.strip()}
    missing = wanted - {e.table for e in entries}
    if missing:
        raise SystemExit(f"not in the inventory: {sorted(missing)}")
    return [e for e in entries if e.table in wanted]


def version(path: str) -> int | None:
    return cr.metadata_version(path.rsplit("/", 1)[-1])


def snapshot(spark, root: str) -> int:
    entries = cr.snapshot(spark, root)
    for e in entries:
        print(f"{e.table}: {e.location} (metadata v{version(e.metadata)})")
    print(f"inventory: {len(entries)} tables → {cr.inventory_path(root)}")
    return 0


def drill(spark, root: str, tables: str | None) -> int:
    """Read each table from its newest metadata file alone and compare it with the live one."""
    entries = selected(cr.load(spark, root), tables)
    failed = []
    for e in entries:
        try:
            file = cr.latest_file(spark, e)
            restored = cr.read_files(spark, file).count()
            live = cr.count(spark, e.table) if cr.exists(spark, e.table) else None
            ok = live is None or restored == live
            newer = version(file) != version(e.metadata)
            print(f"{e.table}: {'OK' if ok else 'MISMATCH'}  restored {restored:,} rows, live "
                  f"{'-' if live is None else f'{live:,}'}; v{version(file)}"
                  f"{f' (inventory had v{version(e.metadata)})' if newer else ''}")
            if not ok:
                failed.append(e.table)
        except Exception:  # noqa: BLE001  report every table
            traceback.print_exc()
            print(f"{e.table}: FAILED")
            failed.append(e.table)
    print(f"drill: {len(entries) - len(failed)} of {len(entries)} tables restore cleanly {failed or ''}")
    return 1 if failed else 0


def live_drill(spark) -> int:
    """Lose and restore a real table, a throwaway copy, through Lakekeeper itself."""
    if cr.exists(spark, DRILL_TABLE):
        cr.purge(DRILL_TABLE)  # left over from an earlier drill
    spark.sql(f"CREATE TABLE lake.{DRILL_TABLE} USING iceberg AS SELECT * FROM lake.{DRILL_SOURCE}")
    spark.sql(f"INSERT INTO lake.{DRILL_TABLE} SELECT * FROM lake.{DRILL_SOURCE} LIMIT 100")  # a 2nd commit
    entry = cr.current(spark, DRILL_TABLE)
    before = cr.count(spark, DRILL_TABLE)
    print(f"{DRILL_TABLE}: {before:,} rows at {entry.location}")
    try:
        cr.forget(DRILL_TABLE)
        assert not cr.exists(spark, DRILL_TABLE), "still in the catalog after forget"
        print(f"{DRILL_TABLE}: gone from the catalog, files kept")
        file = cr.register(spark, entry)
        after = cr.count(spark, DRILL_TABLE)
        ok = after == before
        print(f"{DRILL_TABLE}: re-registered from {file.rsplit('/', 1)[-1]}: {after:,} rows "
              f"{'= before: OK' if ok else f'!= {before:,}: MISMATCH'}")
        return 0 if ok else 1
    finally:
        cr.purge(DRILL_TABLE)
        print(f"{DRILL_TABLE}: dropped with its files")


def restore(spark, root: str, tables: str | None, dry_run: bool) -> int:
    entries = selected(cr.load(spark, root), tables)
    failed = []
    for e in entries:
        if cr.exists(spark, e.table):
            print(f"{e.table}: in the catalog, skipped")
            continue
        try:
            if dry_run:
                print(f"{e.table}: would register {cr.latest_file(spark, e)}")
                continue
            file = cr.register(spark, e)
            print(f"{e.table}: registered from {file}: {cr.count(spark, e.table):,} rows")
        except Exception:  # noqa: BLE001  report every table
            traceback.print_exc()
            print(f"{e.table}: FAILED")
            failed.append(e.table)
    print(f"restore: {len(failed)} failed {failed or ''}")
    return 1 if failed else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["snapshot", "drill", "live-drill", "restore"])
    p.add_argument("--tables", help="comma-separated namespace.table (drill, restore)")
    p.add_argument("--dry-run", action="store_true", help="restore: say what would be registered")
    args = p.parse_args(argv)

    root = dest_root()
    # No cached tables: live-drill and restore must see the catalog as it is after each change.
    spark = get_spark(f"catalog-{args.command}", extra={"spark.sql.catalog.lake.cache-enabled": "false"})
    try:
        if args.command == "snapshot":
            return snapshot(spark, root)
        if args.command == "drill":
            return drill(spark, root, args.tables)
        if args.command == "live-drill":
            return live_drill(spark)
        return restore(spark, root, args.tables, args.dry_run)
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
