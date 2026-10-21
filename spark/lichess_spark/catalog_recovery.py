"""Catalog disaster recovery: get every table back if Lakekeeper's Postgres is lost.

The data survives a catalog loss: each table's Parquet files and Iceberg metadata live in the
lake. What's lost is which table is which. Lakekeeper puts tables at <warehouse>/<uuid>/, so
the paths don't say "gold.openings". Hence:

- `snapshot`: an inventory (name → location, current metadata file, comment) at
  <LICHESS_DEST>/_catalog/inventory.json, refreshed weekly by lake-maintenance.
- `restore`: for each inventory table missing from the catalog, the newest metadata file under
  its location (commits after the snapshot included) is registered under its old name.
- `drill`: reads each table straight from its newest metadata file (a read-only static table,
  no catalog) and compares it with the live table row for row. Proves the inventory and the
  files would restore, without touching the catalog.

docs/runbooks/catalog-recovery.md has the steps.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import asdict, dataclass

from lichess_spark import maintenance

INVENTORY = "_catalog/inventory.json"

# Iceberg metadata files: <version>-<uuid>.metadata.json (catalogs), v<version>.metadata.json
# (Hadoop tables), optionally gzipped (.gz.metadata.json or .metadata.json.gz).
_METADATA = re.compile(r"^(?:v(\d+)|(\d+)-[0-9a-fA-F-]+)(?:\.gz)?\.metadata\.json(?:\.gz)?$")


@dataclass(frozen=True)
class Entry:
    table: str  # namespace.table
    location: str
    metadata: str  # the current metadata file when the inventory was written
    comment: str | None = None


def inventory_path(root: str) -> str:
    return f"{root.rstrip('/')}/{INVENTORY}"


def metadata_version(name: str) -> int | None:
    m = _METADATA.match(name)
    return int(m.group(1) or m.group(2)) if m else None


def newest_metadata(names: list[str]) -> str | None:
    """The metadata file with the highest version: the table's last commit."""
    versioned = [(v, n) for n in names if (v := metadata_version(n)) is not None]
    return max(versioned)[1] if versioned else None


def to_json(entries: list[Entry], now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return json.dumps({"written_at": now.isoformat(timespec="seconds"),
                       "tables": [asdict(e) for e in sorted(entries, key=lambda e: e.table)]}, indent=2)


def from_json(text: str) -> list[Entry]:
    return [Entry(**t) for t in json.loads(text)["tables"]]


# --- Spark side -----------------------------------------------------------------------------

def _fs(spark, path: str):
    jvm = spark._jvm
    p = jvm.org.apache.hadoop.fs.Path(path)
    return p, p.getFileSystem(spark._jsc.hadoopConfiguration())


def write_text(spark, path: str, text: str) -> None:
    p, fs = _fs(spark, path)
    out = fs.create(p, True)
    try:
        out.write(bytearray(text.encode()))
    finally:
        out.close()


def read_text(spark, path: str) -> str:
    return spark.read.text(path, wholetext=True).first()[0]


def list_names(spark, directory: str) -> list[str]:
    p, fs = _fs(spark, directory)
    if not fs.exists(p):
        return []
    return [s.getPath().getName() for s in fs.listStatus(p)]


def current(spark, table: str, catalog: str = "lake") -> Entry:
    """A table's inventory entry, from the catalog."""
    name = f"{catalog}.{table}"
    meta = spark.sql(f"SELECT file FROM {name}.metadata_log_entries ORDER BY timestamp DESC LIMIT 1").first()[0]
    rows = {r.col_name: r.data_type for r in spark.sql(f"DESCRIBE TABLE EXTENDED {name}").collect()}
    return Entry(table=table, location=rows["Location"], metadata=meta, comment=rows.get("Comment") or None)


def snapshot(spark, root: str, catalog: str = "lake") -> list[Entry]:
    entries = [current(spark, t, catalog) for t in maintenance.tables(spark)]
    write_text(spark, inventory_path(root), to_json(entries))
    return entries


def load(spark, root: str) -> list[Entry]:
    return from_json(read_text(spark, inventory_path(root)))


def latest_file(spark, entry: Entry) -> str:
    """The newest metadata file under the table's location (may be newer than the inventory's)."""
    directory = f"{entry.location.rstrip('/')}/metadata"
    name = newest_metadata(list_names(spark, directory))
    if not name:
        raise FileNotFoundError(f"{entry.table}: no metadata files in {directory}")
    return f"{directory}/{name}"


def register(spark, entry: Entry, catalog: str = "lake") -> str:
    """Register the table under its old name from its newest metadata file. Returns that file."""
    file = latest_file(spark, entry)
    namespace = entry.table.split(".", 1)[0]
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {catalog}.{namespace}")
    spark.sql(f"CALL {catalog}.system.register_table(table => '{entry.table}', metadata_file => '{file}')")
    return file


def exists(spark, table: str, catalog: str = "lake") -> bool:
    return spark.catalog.tableExists(f"{catalog}.{table}")


def count(spark, table: str, catalog: str = "lake") -> int:
    return spark.table(f"{catalog}.{table}").count()


def read_files(spark, metadata_file: str):
    """The table as of a metadata file, read straight from the lake: no catalog, read-only. Uses
    the job's own storage access (ABFS settings), not Lakekeeper's vended credentials."""
    return spark.read.format("iceberg").load(metadata_file)


# --- Lakekeeper, for the live drill ---------------------------------------------------------

def _lakekeeper(method: str, path: str, env=None) -> tuple[int, dict | None]:
    """Call Lakekeeper's Iceberg REST API as this pod (its service account token)."""
    import os
    import urllib.error
    import urllib.parse
    import urllib.request

    from lichess_spark.session import SA_TOKEN

    env = os.environ if env is None else env
    base = env["LICHESS_CATALOG_URI"].rstrip("/")
    with open(env.get("LICHESS_CATALOG_TOKEN_FILE", SA_TOKEN)) as fh:
        token = fh.read().strip()

    def call(m: str, p: str) -> tuple[int, dict | None]:
        req = urllib.request.Request(base + p, method=m, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode()[:500]}

    warehouse = urllib.parse.quote(env.get("LICHESS_WAREHOUSE", "lichess"))
    status, config = call("GET", f"/v1/config?warehouse={warehouse}")
    if status != 200 or not config:
        raise RuntimeError(f"Lakekeeper config: {status} {config}")
    prefix = config.get("overrides", {}).get("prefix") or config.get("defaults", {}).get("prefix")
    return call(method, f"/v1/{prefix}{path}")


def _table_path(table: str) -> str:
    namespace, name = table.split(".", 1)
    return f"/namespaces/{namespace}/tables/{name}"


def forget(table: str, env=None) -> None:
    """Take a table out of the catalog at once, keeping its files: what losing the catalog does.
    (Lakekeeper: no purge, and `force` skips the soft-delete that would block re-registering.)"""
    status, body = _lakekeeper("DELETE", f"{_table_path(table)}?purgeRequested=false&force=true", env)
    if status not in (200, 204):
        raise RuntimeError(f"forget {table}: {status} {body}")


def purge(table: str, env=None) -> None:
    """Drop a table and delete its files at once (the drill's own copy only)."""
    status, body = _lakekeeper("DELETE", f"{_table_path(table)}?purgeRequested=true&force=true", env)
    if status not in (200, 204):
        raise RuntimeError(f"purge {table}: {status} {body}")
