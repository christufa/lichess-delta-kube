"""Lichess archive discovery and resumable downloads."""
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from tqdm.auto import tqdm

BASE_URL    = "https://database.lichess.org"


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
            expected = r.headers.get("Content-Range", "").removeprefix("bytes */")
            if expected.isdigit() and resume == int(expected):
                tmp.replace(dest)
                return
        r.raise_for_status()
        if resume and r.status_code == 200:
            # Server ignored Range: restart rather than append a full archive.
            resume = 0
        if r.status_code == 206:
            if not r.headers.get("Content-Range", "").startswith(f"bytes {resume}-"):
                raise ValueError("Server returned an unexpected download range")
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

        if r.headers.get("content-length") and tmp.stat().st_size != total:
            raise IOError("Incomplete archive download; partial file retained for retry")

    tmp.rename(dest)


def download_archives(args, spark, root):
    """Cache compressed archives and publish a manifest for extraction."""
    import re
    from common import selected_period, write_json, checkpoint, record_completion

    year, month = selected_period(args.year, args.month)
    selected = {"variant": args.variant, "month": f"{year:04d}-{month:02d}"}
    if checkpoint(root, args, selected, "upload"):
        print(f"Already complete: {selected['variant']}/{selected['month']}", flush=True)
        write_json(root / "downloads.json", [])
        return
    # A new run can resume a previously committed Delta month without raw files.
    saved = checkpoint(root, args, selected, "insert")
    if saved:
        write_json(root / "downloads.json", [saved])
        return
    saved = checkpoint(root, args, selected, "extract")
    if saved:
        write_json(root / "downloads.json", [saved])
        return
    links = sorted(set(fetch_links(args.variant, year, month)))
    if not links:
        raise ValueError("No matching archives found")
    if args.limit:
        links = links[:args.limit]
    archives = []
    for name, url in links:
        match = re.fullmatch(r"lichess_db_(\w+)_rated_(\d{4}-\d{2})\.pgn\.zst", name)
        if not match:
            raise ValueError(f"Unexpected archive filename: {name}")
        raw = root.parent.parent / "raw" / name
        if not raw.exists():
            download_file(url, raw)
        archive = dict(variant=match[1], month=match[2], raw_path=str(raw), source_url=url)
        record_completion(root, args, archive, "download")
        archives.append(archive)
    write_json(root / "downloads.json", archives)


if __name__ == "__main__":
    from common import run_task
    run_task(download_archives, "download")
