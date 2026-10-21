# Runbook: recovering the Iceberg catalog

The lake's data never lives in the catalog. Every table's Parquet files and Iceberg metadata
are in ADLS. Lakekeeper's Postgres (`lakekeeper-catalog-pg`, one instance, no backups) only
records **which table is which**: name → location → current metadata file. Losing that
database makes every table disappear from Spark, Trino, the API and the chat, while the files
stay where they are.

Lakekeeper puts each table at `<lake>/bootstrap/<uuid>/`, so the paths don't say which table is
which. That's what the inventory is for.

## What's in place

- **Inventory:** `<lake>/_catalog/inventory.json` has every table's name, location, current metadata
  file and comment. `catalog_recovery snapshot` writes it every Sunday after maintenance
  (`lake-maintenance`), so a new table is covered within a week.
- **Weekly drill:** `catalog_recovery drill` runs right after the snapshot.
  - It reads each table straight from its newest metadata file (no catalog, read-only) and compares the row count with the live table.
  - A mismatch or a missing file fails the run, which emails (WorkflowFailed).
- **Live drill, on demand:** `catalog_recovery live-drill` copies `gold.rating_distribution` to `ops.dr_drill`, removes it from Lakekeeper with its files kept (as a lost catalog would), re-registers it from the inventory and its files, compares the rows, and drops the copy.
- **Soft delete:** a table dropped by mistake isn't a catalog loss. The warehouse keeps dropped tables recoverable for 7 days (`delete-profile`, `deploy/lichess/workflowtemplate-lakekeeper-bootstrap.yaml`); restore it through Lakekeeper ([docs](https://docs.lakekeeper.io/docs/nightly/concepts/)) instead.

Run any of them by hand with `deploy/examples/run-catalog-recovery.yaml` (set `args`).

## After losing the catalog database

1. **Get an empty, working Lakekeeper.**
   - Argo CD recreates the CloudNativePG cluster from `deploy/` once its PVC is gone (delete the stuck `Cluster` or pod if needed).
   - Lakekeeper migrates the empty database when it starts.
   - Check: `kubectl get clusters.postgresql.cnpg.io -n lakekeeper` is healthy, and the Lakekeeper pod is Ready.
2. **Recreate the warehouse and the readers' grants.**
   ```powershell
   kubectl create -n lichess -f deploy/examples/run-lakekeeper-bootstrap.yaml
   ```
   This creates warehouse `lichess` on the same storage prefix. Its ID changes, but clients use the name.
   - If OpenFGA's database (`openfga-pg`) was lost too, the bootstrap recreates the grants.
3. **See what would come back.** Set `args` to `["restore", "--dry-run"]` in `deploy/examples/run-catalog-recovery.yaml`, then:
   ```powershell
   kubectl create -n lichess -f deploy/examples/run-catalog-recovery.yaml
   ```
   Each table lists the metadata file it would be registered from: the newest under its location, including commits after the last inventory.
4. **Register them.** Run it again with `args: ["restore"]`. Each table is registered under its old name and counted. Tables the catalog already has are skipped, so it's safe to rerun.
5. **Check:**
   - `args: ["drill"]`: every table OK.
   - `kubectl create -n lichess -f deploy/examples/run-trino-smoke.yaml`
   - The dashboard loads.
6. **Fix the inventory if needed.**
   - A table created after the last snapshot isn't in the inventory. Find its directory under `<lake>/bootstrap/` (its `metadata/*.metadata.json` has the schema and a `comment` property), add it to `inventory.json` by hand, and rerun the restore.
   - If the inventory itself is gone, every table can still be found by its schema and comment. Then rebuild it with `snapshot` once the tables are back.

## If a table comes back wrong

`restore` picks the metadata file with the highest version. Very rarely, that's a commit that
wrote its file but lost the race in the catalog. Iceberg's history is in the files, so:
- compare with the inventory's `metadata` file (`drill` says when they differ: "inventory had vN");
- if needed, take the table out of the catalog again (`catalog_recovery.forget`) and register an earlier file with `CALL lake.system.register_table(...)`.

## If the lake itself is lost

That's beyond this runbook. Rebuild from Lichess's public dumps
(`deploy/examples/run-ingest-backfill.yaml`, then curated, player_games and gold for each month).
The data is public, so the lake is LRS with 7-day soft delete and no other copy
(`infra/platform/storage.tf`).
