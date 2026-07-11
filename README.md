# lichess-games-download

Downloads monthly Lichess game archives from [database.lichess.org](https://database.lichess.org), parses them into structured Parquet files, and uploads them to the HuggingFace dataset [christopher3/lichess-games](https://huggingface.co/datasets/christopher3/lichess-games).

## How it works

```
Lichess HTTP (.pgn.zst)
        │
        │  streamed — never written to disk
        ▼
  zstd decompress
        │
        ▼
  PGN parser (python-chess)
        │  200k games per batch by default
        ▼
  Parquet shard (pandas + pyarrow)
        │
        ▼
  HuggingFace dataset
  data/<variant>/<YYYY-MM>/part-NNNN.parquet
```

Archives are streamed directly from Lichess to HuggingFace — no intermediate files are written to disk. This keeps disk usage well within GitHub Actions' ~14 GB limit even for large monthly dumps.

Each run checks the HF repo first; if `data/<variant>/<YYYY-MM>/part-0000.parquet` already exists the archive is skipped.

## Files

| File | Purpose |
|---|---|
| `src/pipeline.py` | Main entry point — orchestrates the full pipeline |
| `src/01_download.py` | Download `.pgn.zst` archives to disk (local use) |
| `src/02_decompress.py` | Decompress `.pgn.zst` → `.pgn` (local use) |
| `src/03_upload.py` | Parse and upload to HuggingFace (local use) |
| `Dockerfile` | Container image used in CI and local Docker runs |
| `docker-compose.yml` | Local Docker runner with volume + env wiring |
| `.github/workflows/pipeline.yml` | GitHub Actions workflow — runs on the 5th of each month |

## Running locally

**Prerequisites:** Docker, and an HF token with write access to the dataset.

1. Create a `.env` file:
   ```
   HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
   ```

2. Run the pipeline:
   ```bash
   docker compose run pipeline --variant standard --year 2024 --month 1
   ```

   Downloaded archives appear in `./data/raw/` so you can inspect them. Pass `--limit N` to process only the first N files.

3. To use the individual scripts directly (no Docker):
   ```bash
   pip install -r requirements.txt
   python src/01_download.py --variant standard --year 2024 --month 1
   python src/03_upload.py --raw-dir data/raw
   ```

## GitHub Actions setup

1. Push this repo to GitHub.
2. Go to **Settings → Secrets and variables → Actions** and add:
   - `HF_TOKEN` — your HuggingFace write token

The workflow (`.github/workflows/pipeline.yml`) runs automatically on the **5th of each month** and processes the previous month's standard games. You can also trigger it manually via **Actions → Lichess → HuggingFace → Run workflow** and supply custom `variant`, `year`, `month`, or `limit` inputs.

## Dataset layout on HuggingFace

```
christopher3/lichess-games/
└── data/
    └── standard/
        └── 2024-01/
            ├── part-0000.parquet   # 200k games by default
            ├── part-0001.parquet
            └── ...
```

Each Parquet row contains:

| Column | Type | Description |
|---|---|---|
| `source_id` | string | Lichess game ID |
| `variant` | string | e.g. `standard` |
| `event` | string | Time control category |
| `white_username` | string | |
| `white_rating` | int | Elo at time of game |
| `white_rating_diff` | int | Rating change |
| `black_username` | string | |
| `black_rating` | int | |
| `black_rating_diff` | int | |
| `result` | string | `1-0`, `0-1`, or `1/2-1/2` |
| `termination` | string | e.g. `Normal`, `Time forfeit` |
| `played_at` | timestamp | UTC |
| `time_control` | string | e.g. `600+0` |
| `eco` | string | Opening code |
| `opening` | string | Opening name |
| `pgn` | string | Full PGN text |
| `moves` | string | JSON array of move objects |

Each move object: `{"n": 1, "c": "w", "san": "e4", "fen": "...", "eval_cp": 18, "clk": 598}`
