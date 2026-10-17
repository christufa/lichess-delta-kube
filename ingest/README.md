# lichess-ingest

Bootstraps the full Lichess standard-games history into raw Parquet in ADLS,
**one month at a time**, from your own machine. Local disk only needs room for
about two months; everything else lives in Azure.

For each month, oldest first:

```text
download .pgn.zst (resumable) ─► verify sha256 ─► parse in parallel ─► Parquet files (~512 MB)
   ─► upload each file as it closes ─► check game count == Lichess counts.txt
   ─► write manifest ─► delete local files
```

The next month downloads while the current one parses. Re-running skips months
that are done, so you can stop (Ctrl+C) and resume any time.

## Output layout

```text
<dest>/raw/games/month=YYYY-MM/part-<run>-<worker>-<n>.parquet
<dest>/_state/raw_games/YYYY-MM.json      manifest for the month
```

- **Columns:** every PGN header tag Lichess uses as a string column,
  `movetext` as one string (no move replay), `extra_tags` for anything unexpected,
  plus `month`, `_source_file`, `_run_id`, `_ingested_at`.
- **The manifest is the source of truth.** It's written only after all files are
  uploaded and the row count equals Lichess's published game count. After that,
  any other file under the month (an earlier run, a crashed attempt) is deleted.
- **Upstream changes:** if Lichess republishes a month (new sha256), the next run
  re-ingests it and replaces the old files.
- Registering these files in Iceberg (`raw.games`) is a separate step once the
  catalog is chosen (#9, #23).

## Setup

1. **Storage:** the lake from `infra/platform` (see `infra/README.md`). Your
   user gets the **Storage Blob Data Contributor** role there, and your IP must
   be on the lake's firewall. `terraform output lichess_dest` (in
   `infra/platform`) prints the value for `LICHESS_DEST`.
2. **Credentials:** copy `.env.example` to `.env` and fill in `LICHESS_DEST` and a
   SAS token:
   ```powershell
   az login
   az storage container generate-sas --account-name <account> --name <container> `
     --permissions racwdl --expiry 2026-10-17 --auth-mode login --as-user -o tsv
   ```
   A user-delegation SAS lasts at most 7 days; generate a new one if a long
   bootstrap outlives it.
3. **Build:**
   ```powershell
   cd ingest
   docker compose build
   ```

## Run

```powershell
# What's done and what's left
docker compose run --rm ingest plan

# Try the small early months first
docker compose run --rm ingest run --months 2013

# Everything else, as many sessions as you like (Ctrl+C is safe)
docker compose run --rm ingest run

# Or a slice
docker compose run --rm ingest run --months 2020-01:2020-12
docker compose run --rm ingest run --max-months 5
```

Progress is logged every 30 s per month (percent, ETA) plus overall months
remaining. Each finished (or failed) month's record is written to the destination as
`_state/runs/<run_id>/<month>.json` and appended to `/work/runs.jsonl`:

```powershell
docker compose run --rm --entrypoint cat ingest /work/runs.jsonl > runs.jsonl
```

### Useful options (`run`)

| Option | Default | |
|---|---|---|
| `--workers` | 6 | Parser processes. The spike showed ~4–5 cores is enough; extra workers absorb the higher compression level |
| `--chunk-mb` | 128 | Decompressed MB per chunk (one Parquet row group) |
| `--file-mb` | 512 | Roll to a new Parquet file at this size |
| `--zstd-level` | 9 | Parquet compression; workers had idle time in the spike, so higher levels are nearly free |
| `--prefetch` | 1 | Months downloaded ahead; each needs up to ~35 GB of scratch |
| `--keep-going` | off | Continue with the next month after a failure |
| `--force` | off | Re-ingest months that are already done |
| `--keep-source` | off | Keep the downloaded `.pgn.zst` files |

**Disk:** about `(1 + prefetch) × 35 GB` for downloads plus a few GB of staging.
**Memory:** roughly `workers × (150 MB + 8 × chunk-mb)`, about 7 GB at the defaults.

## Running without Docker

```powershell
cd ingest
py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
az login        # DefaultAzureCredential picks this up; no SAS needed
python -m lichess_ingest --dest abfss://<container>@<account>.dfs.core.windows.net/bootstrap run --months 2013
```

A local directory also works as `--dest`, which is handy for a dry run.

## Development

```bash
pip install -e ".[dev]" && pytest -q
# Blob-storage test against Azurite (npm i -g azurite):
azurite-blob --silent --blobPort 10000 &
AZURITE_BLOB_URL=http://127.0.0.1:10000/devstoreaccount1 pytest -q
```
