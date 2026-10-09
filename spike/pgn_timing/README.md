# pgn-timing — ingestion spike tool (#16)

Times each stage of turning one Lichess monthly dump (`.pgn.zst`) into Parquet
and an Iceberg table, so we know where the time goes before building the real
pipeline.

The parse path never extracts the file to disk and never replays moves:

```text
.pgn.zst ─► zstd stream ─► chunks cut at game boundaries ─► N parser processes ─► Parquet
```

Header tags become string columns, `movetext` stays one string, and unknown tags
land in an `extra_tags` map.

| Command | Measures |
|---|---|
| `download` | Resumable HTTP download of the dump |
| `decompress` | zstd stream alone — the single-stream ceiling |
| `parse` | Decompress → chunk → parallel header parse → Parquet; reports where wall time went |
| `commit` | PyIceberg `add_files` into a local (SQLite-catalog) Iceberg table |
| `pychess` | The old approach (python-chess, replays every move) on a sample |

## Run with Docker (recommended)

Requires Docker Desktop (WSL 2 backend on Windows). Everything runs in a Linux
container, the same way the pipeline will run on AKS.

### One-time setup

In Docker Desktop → **Settings → Resources**:

- **CPUs / memory:** the WSL 2 backend uses all CPUs and half the RAM by
  default, which is plenty. If you've capped them in `%UserProfile%\.wslconfig`,
  raise the limits.
- **Disk:** data lives in the `lichess-data` Docker volume, inside Docker's disk
  image. One run needs ~150 GB at peak (download + Parquet outputs; delete as you
  go). Move the **disk image location** to a large SSD if `C:` is tight.

> Use the named volume rather than bind-mounting a Windows folder (`-v D:\...:/data`).
> Bind mounts from Windows drives are several times slower in Linux containers
> and would distort the parse and commit timings.

### Build

```powershell
cd spike\pgn_timing
docker compose build
```

### Run the spike on one month

Use a recent month (largest file). From `spike\pgn_timing` in PowerShell:

```powershell
$M = "2025-09"
$URL = "https://database.lichess.org/standard/lichess_db_standard_rated_$M.pgn.zst"
function spike { docker compose run --rm spike --report /data/runs.jsonl @args }

# 1. Download (resumable: re-run the same command after an interruption)
spike download $URL --out /data/$M.pgn.zst

# 2. Decompression alone
spike decompress /data/$M.pgn.zst

# 3. Full parse to Parquet at several worker counts
foreach ($w in 4, 8, 16, 24) {
  spike parse /data/$M.pgn.zst --workers $w --out /data/parquet-$w
}

# 4. Iceberg commit of one Parquet output
spike commit /data/parquet-16 --warehouse /data/warehouse

# 5. Old approach for comparison
spike pychess /data/$M.pgn.zst --games 20000

# Copy the results out of the volume
docker compose run --rm --entrypoint cat spike /data/runs.jsonl > runs.jsonl
```

Housekeeping:

```powershell
# Free space between runs
docker compose run --rm --entrypoint rm spike -rf /data/parquet-4 /data/parquet-8
# Remove everything when done
docker volume rm lichess-data
```

Quick tries without the full month: add `--limit-mb 2000` to `decompress` or
`parse`, or pass the URL instead of a file to stream download → parse directly.

## Run without Docker

```powershell
cd spike\pgn_timing
py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[iceberg,pychess,dev]"
python -m pgn_timing --report runs.jsonl parse D:\lichess\2025-09.pgn.zst --workers 16 --out D:\lichess\parquet-16
```

(Same commands as above, with local paths. On Linux/macOS use `.venv/bin/activate`.)

## Development

```bash
pip install -e ".[iceberg,dev]" && pytest -q   # or: docker build --target test .
```

## Reading the result

`parse` reports where the wall-clock time went:

| Field | Meaning |
|---|---|
| `main_decompress_read_s` | Main process reading + decompressing the single zstd stream |
| `main_waiting_on_workers_s` | Main process blocked because parsers were busy |
| `worker_parse_s_total` / `worker_write_s_total` | CPU time summed across parser processes |
| `bottleneck_hint` | Which side dominated |

If adding workers stops helping and `main_decompress_read_s` ≈ `wall_s`,
decompression is the ceiling and the parser is fast enough. If workers stay the
bottleneck at high core counts, that's the case for the Rust parser (#22). On
hybrid CPUs, worker counts past the P-core count show what E-cores add.

Paste `runs.jsonl` into #16 when done.

## Synthetic baseline

On a 2-core dev container with synthetic games (not representative of real
compression ratios): ~43k games/s per parser process, 73k games/s with 2
workers, Parquet encoding ~7% of worker time.
