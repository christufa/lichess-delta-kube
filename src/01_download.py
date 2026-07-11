import argparse
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from tqdm.auto import tqdm

BASE_URL    = "https://database.lichess.org"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "raw"


def fetch_links(
    variant: str | None = None,
    year: int | None = None,
    month: int | None = None,
) -> list[tuple[str, str]]:
    resp = requests.get(BASE_URL + "/", timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if not href.endswith(".pgn.zst"):
            continue
        url = href if href.startswith("http") else f"{BASE_URL}/{href.lstrip('/')}"
        name = url.split("/")[-1]
        if variant and f"_{variant}_" not in name:
            continue
        if year and f"_{year}-" not in name:
            continue
        if month and f"-{month:02d}." not in name:
            continue
        links.append((name, url))

    return links


def download_file(url: str, dest: Path, chunk: int = 1 << 20) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    headers: dict[str, str] = {}
    resume = tmp.stat().st_size if tmp.exists() else 0
    if resume:
        headers["Range"] = f"bytes={resume}-"

    with requests.get(url, stream=True, headers=headers, timeout=60) as r:
        if r.status_code == 416:
            tmp.rename(dest)
            return
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) + resume
        mode  = "ab" if resume else "wb"

        with open(tmp, mode) as f, tqdm(
            desc=dest.name,
            total=total,
            initial=resume,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            leave=False,
        ) as bar:
            for data in r.iter_content(chunk):
                f.write(data)
                bar.update(len(data))

    tmp.rename(dest)


def main(
    variant: str = "standard",
    year: int | None = None,
    month: int | None = None,
    limit: int | None = None,
    out: Path = DEFAULT_OUT,
) -> None:
    print(f"Fetching index from {BASE_URL} …")
    links = fetch_links(variant, year, month)
    if not links:
        print("No matching files found. Check --variant / --year.", file=sys.stderr)
        sys.exit(1)

    if limit:
        links = links[:limit]

    out = Path(out)
    print(f"Found {len(links)} file(s) → {out}")

    for name, url in tqdm(links, desc="Files", unit="file"):
        dest = out / name
        if dest.exists():
            tqdm.write(f"  skip (exists): {name}")
            continue
        tqdm.write(f"  downloading:   {name}")
        download_file(url, dest)

    print("Download complete.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Download Lichess PGN archives")
    p.add_argument("--variant", default="standard",
                   help="Game variant, e.g. standard, chess960, antichess (default: standard)")
    p.add_argument("--year",  type=int, default=None, help="Filter by year, e.g. 2024")
    p.add_argument("--month", type=int, default=None, help="Filter by month 1-12, e.g. 3 for March")
    p.add_argument("--limit", type=int, default=None, help="Max number of files to download")
    p.add_argument("--out",   default=str(DEFAULT_OUT), help="Output directory for .pgn.zst files")
    a = p.parse_args()
    main(a.variant, a.year, a.month, a.limit, Path(a.out))
