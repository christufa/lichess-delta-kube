"""Lichess → HuggingFace pipeline: download, decompress, parse to Parquet, upload."""
import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

from huggingface_hub import HfApi

HERE          = Path(__file__).resolve().parent
DEFAULT_REPO  = os.getenv("HF_DATASET", "christopher3/lichess-games")
DEFAULT_SHARD = int(os.getenv("HF_SHARD_SIZE", "200000"))
DEFAULT_RAW   = HERE.parent / "data" / "raw"

_VARIANT_RE = re.compile(r"lichess_db_(\w+)_rated_")
_MONTH_RE   = re.compile(r"_rated_(\d{4}-\d{2})\.")


def _already_uploaded(api: HfApi, repo_id: str, filename: str) -> bool:
    """Skip only if a .done marker from a completed upload exists."""
    m_v = _VARIANT_RE.search(filename)
    m_m = _MONTH_RE.search(filename)
    if not m_v or not m_m:
        return False
    marker = f"data/{m_v.group(1)}/{m_m.group(1)}/.done"
    try:
        return api.file_exists(repo_id, marker, repo_type="dataset")
    except Exception:
        return False


def _load(filename: str):
    name = filename.replace(".", "_")
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # register before exec so pickle can find it by module name
    spec.loader.exec_module(mod)
    return mod


def main(
    variant: str = "standard",
    year: int | None = None,
    month: int | None = None,
    limit: int | None = None,
    repo: str = DEFAULT_REPO,
    raw_dir: Path = DEFAULT_RAW,
    shard_size: int = DEFAULT_SHARD,
    workers: int | None = None,
    shard_dir: Path | None = None,
    private: bool = False,
) -> None:
    token = os.getenv("HF_TOKEN")
    if not token:
        print("HF_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)

    dl = _load("01_download.py")
    ul = _load("03_upload.py")

    api = HfApi(token=token)
    api.create_repo(repo, repo_type="dataset", exist_ok=True, private=private)

    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching index …", flush=True)
    links = dl.fetch_links(variant, year, month)
    if not links:
        print("No matching files found.", file=sys.stderr)
        sys.exit(1)
    if limit:
        links = links[:limit]
    print(f"Found {len(links)} archive(s) — uploading to {repo}\n")

    for i, (filename, url) in enumerate(links, 1):
        print(f"[{i}/{len(links)}] {filename}", flush=True)

        if _already_uploaded(api, repo, filename):
            print("  already on HF — skipping\n", flush=True)
            continue

        zst_dest = raw_dir / filename

        # Download
        if zst_dest.exists():
            print(f"  already downloaded ({zst_dest.stat().st_size / 1024**3:.2f} GB) — skipping download", flush=True)
        else:
            dl.download_file(url, zst_dest)

        # Parse (streaming decompress) → Parquet shards → upload
        print(f"  parsing and uploading (shard size: {shard_size:,}, workers: {workers or 'auto'}) …", flush=True)
        ul.parse_and_upload(api, repo, zst_dest, shard_size=shard_size, workers=workers, shard_dir=shard_dir)

        # Cleanup
        if zst_dest.exists():
            zst_dest.unlink()
        print("  local files deleted\n", flush=True)

    print("Done.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Lichess → HuggingFace pipeline")
    p.add_argument("--variant",    default="standard",
                   help="Game variant, e.g. standard, chess960, antichess")
    p.add_argument("--year",       type=int, default=None,
                   help="Filter by year, e.g. 2024")
    p.add_argument("--month",      type=int, default=None,
                   help="Filter by month 1–12")
    p.add_argument("--limit",      type=int, default=None,
                   help="Process at most N archives")
    p.add_argument("--repo",        default=DEFAULT_REPO,
                   help="HuggingFace dataset repo (overrides HF_DATASET env var)")
    p.add_argument("--shard-size",  type=int, default=DEFAULT_SHARD,
                   help="Games per Parquet shard (overrides HF_SHARD_SIZE env var)")
    p.add_argument("--workers",     type=int, default=None,
                   help="Parser worker processes (default: all CPU cores)")
    p.add_argument("--shard-dir",   default=None,
                   help="Directory to stage Parquet shards before uploading (default: system temp). "
                        "Useful on machines where temp is small — point to a volume with free space.")
    p.add_argument("--private",     action="store_true",
                   help="Create the HF dataset as private")
    a = p.parse_args()
    main(a.variant, a.year, a.month, a.limit, a.repo,
         DEFAULT_RAW, a.shard_size, a.workers,
         Path(a.shard_dir) if a.shard_dir else None,
         a.private)
