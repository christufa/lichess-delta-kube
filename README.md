# lichess-games-download

Incrementally import the official [Lichess Standard Chess Games dataset](https://huggingface.co/datasets/Lichess/standard-chess-games)
from Hugging Face into a Unity Catalog Delta table on Databricks.

```text
HF monthly Parquet shards -> volume cache -> columnar time conversion -> Delta
```

The serverless job has one installed wheel task, `sync`. It discovers published
months, downloads shards concurrently, and commits each month before starting
the next. There is no PGN download/extraction, chess move parsing, or HF upload.
The source is public and CC0; no HF token or secret scope is required.

## What gets loaded

Source: `Lichess/standard-chess-games`, `data/year=YYYY/month=MM/train-*.parquet`.
Live inspection on October 3, 2026 found 153 months (January 2013 through September
2025), totaling about 4.94 TB of source Parquet. Publication may lag the original
Lichess PGN archives. Discovery uses the repository contents, not a calendar guess.

The original HF columns and values are retained, including `Event`, `Site`,
`White`, `Black`, ratings, `UTCDate`, `ECO`, `Opening`, `TimeControl`, and
`movetext`. Arrow time columns such as `UTCTime` are converted to strings
(e.g. `12:34:56.123`) for Spark compatibility; nulls remain null. Date and numeric
types are preserved. `variant=standard` and `archive_month=YYYY-MM` are added
as Delta partition columns. Source revision and shard identities are recorded
in the run plan and completion checkpoints.

This schema does not contain the previous pipeline's derived per-move FENs,
UCI moves, parsed clock/evaluation JSON, or reconstructed PGN column. The original
clock/evaluation comments remain in `movetext` where present. Normalization uses
bounded Arrow batches of 8,192 rows and never interprets chess moves.

The default destination is **`brikt.lichess_dev.games_hf`** in dev and
**`brikt.lichess.games_hf`** in prod. Existing `games` tables and old PGN checkpoints
are left intact; the new format uses a separate checkpoint identity. Do not point
this importer at the old enriched table without an explicit schema migration.

## Parameters and schedule

| Parameter | Default | Behavior |
| --- | --- | --- |
| `year`, `month` | `0`, `0` | Sync all published months missing or changed locally |
| `limit` | `0` | Maximum pending months per run; 0 means all |
| `workers` | `4` | Concurrent shard download/preparation workers, 1-16 |

Set both year and month for an explicit month. Missing explicit months fail
clearly; there is no PGN fallback. Months run oldest first. `limit` counts pending
months after unchanged completed months have been excluded. Each selected month
always includes all its shards; incomplete upstream shard sets fail discovery.

The dev schedule remains the 5th of each month at 06:00 America/New_York; prod is
paused by default. Each scheduled run discovers all available months, so late
publications are picked up on a subsequent run. The first unrestricted run is a
full historical backfill. No multi-terabyte download is started by deployment
itself, but the next enabled scheduled run will perform that backfill.

## Setup and deployment

Serverless jobs, Unity Catalog, and outbound access to Hugging Face, its download
storage endpoints, and PyPI are required. The runtime supplies Spark, Delta and
PyArrow; the task installs the pinned HF client with Xet support and tqdm.
The job identity must be able to read/write its volume and create/write its table.

```powershell
python -m pip install -r requirements-dev.txt
databricks auth login --host https://YOUR_WORKSPACE
# Only bootstrap these if absent (already created for brikt dev):
databricks schemas create lichess_dev brikt -p brikt
databricks volumes create brikt lichess_dev staging MANAGED -p brikt
databricks bundle validate -p brikt -t dev
databricks bundle deploy -p brikt -t dev --fail-on-active-runs
# Smallest available month for a deployed smoke test:
databricks bundle run -p brikt -t dev lichess_pipeline --params year=2013,month=1
# One pending month at a time:
databricks bundle run -p brikt -t dev lichess_pipeline --params limit=1
# Full backfill / subsequent incremental sync:
databricks bundle run -p brikt -t dev lichess_pipeline
```

The wheel artifact volume must exist before deployment. Deploy identity needs
WRITE VOLUME and job identity needs READ VOLUME for installation. Bundle variables
are `catalog`, `schema`, `volume`, `table`, and `schedule_status`; use
`BUNDLE_VAR_<name>` or `--var 'table=another_table'` to override them. `.env` is
excluded from deployment and is not used. The installed entry point is `sync`.

Local changes do not modify an active run. A run started under the old deployment
still has its original extract/insert/upload tasks. Deployment uses
`--fail-on-active-runs`; finish or explicitly manage that run before replacing the
job definition. No existing HF dataset, Delta table, or old checkpoint is deleted.

## GitHub Actions deployment

`.github/workflows/databricks.yml` runs **Tests and wheel** on pull requests to
`main`. Pushes/merges to `main` run the same checks and then validate and deploy
the existing `dev` bundle. You can also select **Run workflow** on `main` to retry
a deployment. The workflow deploys the job definition; it does not start a data
run or deploy the prod target.

The `databricks-dev` GitHub environment is restricted to the `main` branch and
has already been configured with:

- Variable `DATABRICKS_HOST`: the brikt workspace URL.
- Variable `DATABRICKS_DEPLOY_USER`: `chris.lavalle00@gmail.com`.
- Secret `DATABRICKS_TOKEN`: a dedicated 90-day deployment token for that user.

The current token expires **January 1, 2027 at 19:07 UTC**. Rotate it before then
by creating a replacement Databricks token and updating `DATABRICKS_TOKEN` under
GitHub Settings -> Environments -> databricks-dev. PR test jobs have no access to deployment secrets.

Deployment verifies the authenticated user before updating the bundle. This
preserves the existing user-scoped dev deployment and avoids creating a second
scheduled job under another identity. Deployments are serialized and bundle
locking is enabled. Deployment fails while a data run is active; rerun the
GitHub deployment after the data run ends. It does not cancel that run.

For longer-term automation, migrate to a service principal with GitHub OIDC and
explicitly migrate/bind the existing bundle state and job permissions. Simply
swapping the identity would create a different dev deployment.

You can now require the **Tests and wheel** check in `protect-main` after its
first successful GitHub run. Deployment runs after merge and should not be a
required PR check.

## Reliability and storage

One job run is allowed at a time. The task has two automatic retries with a
60-second minimum interval. Do not have another job concurrently write to the
same table: Delta version attribution assumes this importer owns table writes.

`runs/<run-id>/hf-plan.json` pins the exact repository commit and shard list for
repairs. Each month gets a fingerprint from its file paths, sizes and content
identities. A new repository commit does not cause unchanged months to reload;
changed month files trigger partition replacement. New runs discover fresh source
state; repairs keep the original plan. Changing selection parameters requires a
new run. Removed upstream months are not automatically deleted from Delta.

Downloaded files are cached under `hf/<fingerprint>/raw/` and Spark-compatible
Parquet under `hf/<fingerprint>/prepared/`, relative to the volume root. The HF
client resumes interrupted downloads. Completed prepared shards have row-count
and size markers and can be reused on retry. Partial prepared files are excluded
from Spark reads. Source and prepared caches are retained, so budget for both
copies plus the Delta table; cleanup is a separate operation.

For each month, Spark loads explicit prepared file paths and replaces only the
matching `variant`/`archive_month` partition. The committed Delta snapshot row
count is checked against source Parquet metadata. Only then is an `insert.json`
checkpoint written under `history/<source-format-table-hash>/standard/YYYY-MM/`.
A retry after a failed write/verification safely replaces the month again.
`runs/<run-id>/delta.json` records months completed by that run. An unchanged
completed month requires no shard download or Delta write on subsequent runs.
Checkpoint reuse assumes the table has not been manually deleted or changed.

## Progress and diagnostics

`tqdm` displays month progress, per-month file progress, row preparation progress,
and the HF client's download progress. Structured JSON stdout logs remain useful
when Databricks renders terminal progress bars poorly:

- `source.discovered`: pinned revision, available month range, file count and bytes.
- `sync.selection` / `sync.plan`: number of selected pending months.
- `download.progress`: completed files/bytes versus totals, row count and effective
  staging bytes/sec (includes preparation and cache hits, not pure network speed).
- `parquet.prepare.progress`: rows/total rows and rows/sec approximately every 30s.
- `delta.write`, `delta.verify`, `month.download`, `source.discover`, `task`:
  start/completion/failure plus a heartbeat every 60 seconds while pending.
- `sync.progress` / `sync.summary`: completed months and rows.

Heartbeats indicate liveness, not a fabricated Spark percentage or ETA. Logs have
UTC timestamps and run IDs and redact token patterns and URL query strings.

## Validation

```powershell
python -m pytest -q
python -m build --wheel
```

Tests cover revision pinning, complete shard discovery, changed-source detection,
Arrow value/null preservation, checkpoint isolation, retry behavior, partition
replacement and verification failures. Local tests mock Spark; a deployed smoke
run is required to verify actual Databricks permissions and Delta integration.
