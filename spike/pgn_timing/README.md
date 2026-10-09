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

## Install

```bash
cd spike/pgn_timing
python -m venv .venv && . .venv/bin/activate
pip install -e ".[iceberg,dev]"        # add ,pychess for the python-chess baseline
pytest -q
```

## Running the spike on one month (Windows / PowerShell)

Use a recent month (largest file). You need ~150 GB free on a fast SSD: the
download, plus one Parquet output per worker count you try (delete as you go).
Pick the output drive accordingly (`D:\lichess` below).

```powershell
cd spike\pgn_timing
py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[iceberg,pychess,dev]"

$M = "2025-09"
$URL = "https://database.lichess.org/standard/lichess_db_standard_rated_$M.pgn.zst"
$D = "D:\lichess"; New-Item -ItemType Directory -Force $D | Out-Null

# 1. Download (resumable: re-run the same command after an interruption)
python -m pgn_timing --report runs.jsonl download $URL --out "$D\$M.pgn.zst"

# 2. Decompression alone - the ceiling for a single zstd stream
python -m pgn_timing --report runs.jsonl decompress "$D\$M.pgn.zst"

# 3. Full parse to Parquet at several worker counts
foreach ($w in 4, 8, 16, 24) {
  python -m pgn_timing --report runs.jsonl parse "$D\$M.pgn.zst" --workers $w --out "$D\parquet-$w"
}

# 4. Iceberg commit of one Parquet output (local SQLite catalog)
python -m pgn_timing --report runs.jsonl commit "$D\parquet-16" --warehouse "$D\warehouse"

# 5. Old approach for comparison (python-chess replays every move)
python -m pgn_timing --report runs.jsonl pychess "$D\$M.pgn.zst" --games 20000
```

Close other heavy apps while it runs; on hybrid CPUs (P-cores + E-cores) the
worker counts past the P-core count show how much E-cores add.

## Running on Linux (VM or WSL)

On an Azure VM in the target region with ~16 cores and a local SSD:

```bash
M=2025-09
URL=https://database.lichess.org/standard/lichess_db_standard_rated_$M.pgn.zst

# 1. Download (resumable: re-run the same command after an interruption)
pgn-timing --report runs.jsonl download $URL --out /mnt/data/$M.pgn.zst

# 2. Decompression alone — the ceiling for a single zstd stream
pgn-timing --report runs.jsonl decompress /mnt/data/$M.pgn.zst

# 3. Full parse to Parquet; repeat with different worker counts
for w in 4 8 16; do
  pgn-timing --report runs.jsonl parse /mnt/data/$M.pgn.zst --workers $w --out /mnt/data/parquet-$w
done

# 4. Iceberg commit of the Parquet files (local SQLite catalog)
pgn-timing --report runs.jsonl commit /mnt/data/parquet-16 --warehouse /mnt/data/warehouse

# 5. Old approach for comparison (python-chess replays every move)
pgn-timing --report runs.jsonl pychess /mnt/data/$M.pgn.zst --games 20000
```

Quick tries without the full month:

- `--limit-mb 2000` on `decompress` or `parse` stops after 2 GB of decompressed PGN.
- `parse` and `decompress` accept the URL directly, which streams download →
  decompress → parse with no local file.
- Omit `--out` on `parse` to time Parquet encoding without writing files.

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
bottleneck at high core counts, that's the case for the Rust parser (#22).

Please paste `runs.jsonl` into #16 when done.

## Synthetic baseline

On a 2-core dev container with synthetic games (not representative of real
compression ratios): ~43k games/s per parser process, 73k games/s with 2
workers, Parquet encoding ~7% of worker time.
