# lichess-games-download

A Databricks bundle that downloads monthly Lichess archives, parses games with
Spark, stores them in a Unity Catalog Delta table, and exports Parquet to the
Hugging Face dataset `christopher3/lichess-games`.

```text
Lichess .pgn.zst -> Unity Catalog volume -> JSONL game chunks
                                              |
                                     Spark mapInPandas
                                              |
                                  Delta games table
                                              |
                                   Spark Parquet export
                                              |
                                      Hugging Face
```

## Project structure

The pipeline keeps application code in `src/` and job configuration in `resources/`.

| File | Purpose |
| --- | --- |
| `databricks.yml` | Bundle variables and dev/prod targets |
| `resources/lichess_job.yml` | Four dependent tasks with automatic retries |
| `src/lichess_pipeline/download.py` | Download compressed archives and record their paths |
| `src/lichess_pipeline/extract.py` | Decompress and split games into JSONL chunks |
| `src/lichess_pipeline/insert.py` | Parse games with Spark and insert into Delta |
| `src/lichess_pipeline/upload.py` | Export a Delta snapshot and upload to Hugging Face |
| `src/lichess_pipeline/common.py`, `src/lichess_pipeline/schema.py` | Task configuration, manifests and dataset columns |
| `src/lichess_pipeline/game_parser.py` | Headers, moves, clocks, evaluations and FEN parser |
| `src/lichess_pipeline/observability.py` | Structured logs and operation heartbeats |
| `pyproject.toml` | Installable wheel and four task entry points |
| `requirements-dev.txt` | Dependencies for tests and wheel builds |

## Pipeline tasks

One Databricks job orchestrates four installed Python wheel tasks:

```text
download -> extract -> insert -> upload
```

Each task has its own entry point, dependencies and retry policy (two retries,
with a 60-second minimum interval). Tasks use serverless compute with a separate dependency environment for each
stage. No existing cluster is required. The bundle builds a versioned wheel and
uploads it to a Unity Catalog volume. Tasks run the installed entry points, and
Spark workers import the same installed package; they do not open application
scripts under `/Workspace`.

The stages pass durable manifests through the run's Unity Catalog volume directory:

| Stage | Input | Completed output |
| --- | --- | --- |
| Download | Lichess index and selected month | Cached archives + `downloads.json` |
| Extract | `downloads.json` | Complete-game JSONL chunks + `archives.json` |
| Insert | `archives.json` | Delta partitions + versioned `delta.json` |
| Upload | `delta.json` | HF Parquet shards + `.done` marker |

Use **Repair run** on a failed job to rerun the failed stage and its downstream
tasks. For example, an upload failure can be repaired without downloading,
extracting or parsing again. A manifest is published only after its stage
finishes successfully. Completed stages are reused even across new job runs.
For a deliberate rebuild, remove that month's destination-specific checkpoint
directory before starting the full job again.

A single job keeps run IDs, dependencies and repair history together. Separate
Databricks jobs would be useful if stages needed independent schedules or were
shared with other pipelines; this monthly flow does not currently need that.
See [Databricks task dependencies](https://docs.databricks.com/aws/en/jobs/run-if).

## Databricks setup

1. Install the Databricks CLI and authenticate to your workspace:

   ```sh
   databricks auth login --host https://YOUR_WORKSPACE
   ```

2. Use a workspace with serverless jobs and Unity Catalog enabled. The default
   catalog is `brikt`. The job identity needs permission to create a schema,
   volume and table (or access to pre-created equivalents). Serverless compute
   needs outbound access to Lichess, PyPI and Hugging Face. The environment
   supplies Spark, Delta, pandas and PyArrow; each task declares its additional
   dependencies in the job YAML.

3. Store the HF write token in Databricks Secrets:

   ```sh
   databricks secrets create-scope lichess
   databricks secrets put-secret lichess hf-token
   ```

   Enter the token at the prompt. The job reads it only during upload. `.env`
   is excluded from bundle sync and is not used by the pipeline.

4. Install build dependencies and create the artifact volume **before the first
   deployment** (skip creation commands when these resources already exist):

   ```powershell
   python -m pip install -r requirements-dev.txt
   databricks schemas create lichess_dev brikt -p brikt
   databricks volumes create brikt lichess_dev staging MANAGED -p brikt
   ```

   These resources have already been created for the `brikt` dev workspace. For
   prod, bootstrap `brikt.lichess.staging` instead. The deploy identity needs
   WRITE VOLUME and the job identity needs READ VOLUME for wheel installation,
   plus the existing pipeline data privileges. Artifact upload happens before
   task execution, so the job cannot bootstrap its own artifact volume.

5. Validate and deploy with the authenticated profile (PowerShell):

   ```powershell
   databricks bundle validate -p brikt -t dev
   databricks bundle deploy -p brikt -t dev
   # Start processing only when ready:
   databricks bundle run -p brikt -t dev lichess_pipeline --params year=2024,month=1
   ```

   The dev target writes to `brikt.lichess_dev.games` and
   `christopher3/lichess-games-dev`. New HF repositories are private; existing
   repository visibility is unchanged. Override `hf_repo` if needed.

6. Deploy/run `-t prod` for `brikt.lichess.games` and
   `christopher3/lichess-games`. Its schedule defaults to paused; the existing
   dev deployment has the monthly schedule enabled.

Available bundle variables: `catalog`, `schema`, `volume`, `table`,
`hf_repo`, `hf_secret_scope`, `hf_secret_key`, `schedule_status`. Set them using `BUNDLE_VAR_<name>`
or `--var 'catalog=another_catalog'`. Give separate deployments distinct
schemas/volumes if they must run independently.

Job parameters: `variant` (default `standard`), `year`, `month`, `limit` (default
1 archive), and `shard_size` (default 200,000 rows per exported file maximum).
Both year and month default to 0, selecting the previous calendar month at job
start. Explicit dates require both values. Backfill by running once per month.

## Monthly schedule and completion history

The existing **dev job** runs on the **5th of every month at 06:00 America/New_York**,
using the previous calendar month. The first scheduled run after this change is
October 5, 2026, for September 2026. The schedule is defined in the bundle and
explicitly enabled for dev; prod remains paused to avoid duplicate schedules.
Destinations remain `brikt.lichess_dev.games` and `christopher3/lichess-games-dev`.

The 5th allows time for publication: the [Lichess directory listing](https://database.lichess.org/standard/)
shows archive timestamps on the 2nd of the following month. This is a scheduling
buffer, not a publication guarantee. A missing archive fails visibly; repair it
with the original explicit year/month once available. The monthly schedule does
not automatically backfill older missed months.

Successful stages write persistent JSON checkpoints under:

```text
/Volumes/<catalog>/<schema>/<volume>/history/<destination-hash>/<variant>/<YYYY-MM>/
    download.json
    extract.json
    insert.json
    upload.json
```

The destination hash includes the Delta table and HF repository. Each record
contains a UTC completion timestamp, original run ID and source archive metadata.
Insertion adds the Delta version and row count; upload adds the HF commit ID and
shard count. Databricks job runs retain task execution status and logs separately.

- A completed upload causes future runs of that month to skip all data work,
  even if the cached archive has since been removed.
- If insertion completed but upload failed, a new run reuses the recorded Delta
  snapshot without downloading, extracting or parsing again.
- If extraction completed, its game chunks are reused; if only download completed,
  the cached archive is reused. Partial downloads resume using HTTP Range.
- Completion is recorded after each successful stage. An interrupted operation
  before its checkpoint is saved can repeat; Delta partition replacement and HF
  month-folder replacement prevent duplicate rows from those retries.

Keep the history directory. Retain extracted files and Delta versions needed by
unfinished months. Checkpoints assume this pipeline owns its destination data;
manual removal of Delta/HF data is not automatically detected. Existing uploads
from before this checkpoint system are not automatically imported as history.
To deliberately rebuild a month, remove its checkpoint directory for the correct
destination, then run with that explicit year/month. Retaining the raw archive
still avoids downloading it again. No historical data is deleted automatically.

## Storage, parallelism and retries

- Archives are cached under `/Volumes/<catalog>/<schema>/<volume>/raw/`.
  Decompression streams on the driver, writing approximately 64 MiB JSONL files
  with one complete PGN per line. It never loads an archive into memory.
- Spark distributes these files and parses games using `mapInPandas`; small
  output batches bound expanded move-history memory while creating potentially large move histories.
  A single compressed archive is not independently splittable: downloading and
  splitting remain driver work. Spark accelerates parsing and table/export I/O.
- Delta is partitioned by `variant` and `archive_month`. A rerun atomically
  replaces that month's partition using `replaceWhere`. Invalid PGN fails the
  write rather than silently dropping games. The saved row count is checked
  against the staging manifest before upload can run.
- The upload task reads the recorded Delta version, writes Parquet with Spark,
  then uploads from the volume without collecting games on the driver. The HF
  folder remains `data/<variant>/<YYYY-MM>/*.parquet`. A `.done` JSON marker
  records the source table, Delta version, row count and shard count. Old shards
  in that same month are replaced in the upload commit. Other months stay intact.
- Task repair can retry the failed stage using manifests under
  `.../runs/<job-run-id>/`. New runs reuse the persistent stage checkpoints
  described below. The job
  allows one concurrent run. Do not have another job write the same table or HF
  month concurrently; version attribution assumes this job owns table writes.
- Staged JSONL, raw downloads and exports are retained for repair and inspection.
  Plan volume capacity for all three, and remove completed run directories and
  raw archives according to your retention policy. Delta versions must remain
  available until upload/repair completes; avoid vacuuming them early.

## Logs and startup troubleshooting

Open the job run, select a task, then view its output. The pipeline emits JSON
lines to stdout, which Databricks captures with the task output. Each event has a
UTC timestamp, level, stage and run ID. Logs include:

- Task start, completion, failure tracebacks and elapsed time, including Spark
  session initialization failures.
- Selected month, discovered archives, download cache hits and checkpoint reuse.
- Download byte counts/rate and extraction game counts/rate approximately every
  30 seconds while work advances.
- A liveness heartbeat every 60 seconds for long download/extraction, Delta write,
  verification, Parquet export and HF upload operations. Heartbeats indicate that
  an operation is still pending, not a percentage complete or proof of progress.
- Row counts, shard sizes/counts, committed Delta versions and HF commit IDs.

Logs omit game content, token values and request headers. The JSON formatter
redacts HF token patterns, bearer credentials and URL query strings in failure
traces. Logging does not require a functioning volume. Completion checkpoints
remain separate from logs; failures never create successful completion events.

A `BlobCustomerSpecifiedEncryptionMismatch` when Databricks tries to open
`/Workspace/.../download.py` occurs **before application startup**. The old script
launcher cannot catch or log that error. This bundle uses installed wheel tasks
with artifacts under `/Volumes/...` to avoid that workspace-file access path.
It does not change Azure encryption keys or repair the underlying storage
configuration. If wheel installation or volume access reports the same error,
inspect Databricks platform output and escalate with the Azure request ID and
Databricks trace ID. Application logs cannot cover failures before the wheel
entry point starts.

After merging this change, redeploy with `databricks bundle deploy -p brikt -t dev`
and repair the failed run using the updated task definitions. The wheel uses a
dynamic build version to avoid reusing a stale serverless environment package.
Do not run `src/lichess_pipeline/*.py` directly; use the wheel entry points or
`python -m lichess_pipeline.<stage>` with `src` on PYTHONPATH.

## Dataset schema

The existing row content is preserved: `game_id`, `variant`, `event`, `site`,
player usernames, Elo ratings, rating differences, titles, teams, result,
termination, UTC `played_at`, time control and its components, opening/ECO,
initial FEN, ply count, PGN and a JSON `moves` string. Numeric game fields use
the existing 16-bit types (32-bit for initial time). Delta adds `archive_month`; this extra
partition field is omitted from HF exports.

Each move retains ply number, color, SAN/UCI, resulting FEN, clock, time spent,
centipawn/mate evaluation, NAGs and human comments.

## Tests

Runtime dependencies are declared in the job YAML. Install the development
requirements to run the parser, staging and download tests locally:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m build --wheel
```

A deployed smoke run is still required to validate job permissions, volume
access, Spark execution and HF credentials. The local tests do not simulate a
Databricks workspace.

References: [Databricks bundle examples](https://docs.databricks.com/aws/en/dev-tools/bundles/examples),
[Delta selective overwrite](https://docs.databricks.com/aws/en/delta/selective-overwrite),
[Hugging Face uploads](https://huggingface.co/docs/huggingface_hub/guides/upload).
