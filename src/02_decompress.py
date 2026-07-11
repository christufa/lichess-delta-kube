import argparse
import sys
from pathlib import Path

import zstandard as zstd
from tqdm.auto import tqdm

DEFAULT_IN  = Path(__file__).resolve().parent.parent / "data" / "raw"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "pgn"
CHUNK       = 1 << 22   # 4 MB decompressed read size


class _ProgressReader:
    """Wraps a binary file and advances a tqdm bar on every compressed read."""

    def __init__(self, fh, bar: tqdm) -> None:
        self._f   = fh
        self._bar = bar

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        self._bar.update(len(data))
        return data


def decompress_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp  = dest.with_suffix(dest.suffix + ".part")
    dctx = zstd.ZstdDecompressor()

    with (
        open(src, "rb") as f_in,
        open(tmp, "wb") as f_out,
        tqdm(
            desc=src.name,
            total=src.stat().st_size,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            leave=False,
        ) as bar,
    ):
        reader = _ProgressReader(f_in, bar)
        with dctx.stream_reader(reader) as stream:
            for chunk in iter(lambda: stream.read(CHUNK), b""):
                f_out.write(chunk)

    tmp.rename(dest)


def main(
    in_dir: Path = DEFAULT_IN,
    out_dir: Path = DEFAULT_OUT,
    delete_source: bool = False,
) -> None:
    in_dir, out_dir = Path(in_dir), Path(out_dir)
    files = sorted(in_dir.glob("*.pgn.zst"))
    if not files:
        print(f"No .pgn.zst files found in {in_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Decompressing {len(files)} archive(s) → {out_dir}")
    for src in tqdm(files, desc="Archives", unit="file"):
        dest = out_dir / src.name.removesuffix(".zst")
        if dest.exists():
            tqdm.write(f"  skip (exists): {dest.name}")
            continue
        tqdm.write(f"  decompressing: {src.name}")
        decompress_file(src, dest)
        if delete_source:
            src.unlink()
            tqdm.write(f"  deleted:       {src.name}")

    print("Decompression complete.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Decompress Lichess .pgn.zst archives")
    p.add_argument("--in-dir",        default=str(DEFAULT_IN),  help="Directory containing .pgn.zst files")
    p.add_argument("--out-dir",       default=str(DEFAULT_OUT), help="Destination directory for .pgn files")
    p.add_argument("--delete-source", action="store_true",      help="Delete each .pgn.zst after decompressing it")
    a = p.parse_args()
    main(Path(a.in_dir), Path(a.out_dir), a.delete_source)
