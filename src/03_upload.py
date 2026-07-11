"""Parse a .pgn or .pgn.zst file into Parquet shards and upload to HuggingFace."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path
from typing import Iterator

import chess
import chess.pgn
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import zstandard as zstd
from huggingface_hub import HfApi
from tqdm.auto import tqdm

DEFAULT_SHARD = int(os.getenv("HF_SHARD_SIZE", "200000"))
DEFAULT_REPO  = os.getenv("HF_DATASET", "christopher3/lichess-games")

_VARIANT_RE = re.compile(r"lichess_db_(\w+)_rated_")
_MONTH_RE   = re.compile(r"_rated_(\d{4}-\d{2})\.")
_CLK_RE     = re.compile(r"\[%clk (\d+):(\d+):(\d+(?:\.\d+)?)\]")
_EVAL_RE    = re.compile(r"\[%eval (#?-?[\d.]+)\]")
_ANN_RE     = re.compile(r"\[%\w+[^\]]*\]")

_NAG_SYMBOLS: dict[int, str] = {1: "!", 2: "?", 3: "!!", 4: "??", 5: "!?", 6: "?!"}

_SCHEMA = pa.schema([
    pa.field("game_id",           pa.string()),
    pa.field("variant",           pa.string()),
    pa.field("event",             pa.string()),
    pa.field("site",              pa.string()),
    pa.field("white_username",    pa.string()),
    pa.field("black_username",    pa.string()),
    pa.field("white_elo",         pa.int16()),
    pa.field("black_elo",         pa.int16()),
    pa.field("white_rating_diff", pa.int16()),
    pa.field("black_rating_diff", pa.int16()),
    pa.field("white_title",       pa.string()),
    pa.field("black_title",       pa.string()),
    pa.field("white_team",        pa.string()),
    pa.field("black_team",        pa.string()),
    pa.field("result",            pa.string()),
    pa.field("termination",       pa.string()),
    pa.field("played_at",         pa.timestamp("us", tz="UTC")),
    pa.field("time_control",      pa.string()),
    pa.field("initial_time_secs", pa.int32()),
    pa.field("increment_secs",    pa.int16()),
    pa.field("eco",               pa.string()),
    pa.field("opening",           pa.large_string()),
    pa.field("initial_fen",       pa.string()),
    pa.field("ply_count",         pa.int16()),
    pa.field("pgn",               pa.large_string()),
    pa.field("moves",             pa.large_string()),
])


def _parse_clk(comment: str) -> int | None:
    m = _CLK_RE.search(comment)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + round(float(m.group(3)))


def _parse_eval(comment: str) -> tuple[int | None, int | None]:
    """Returns (eval_cp, eval_mate); one will always be None."""
    m = _EVAL_RE.search(comment)
    if not m:
        return None, None
    v = m.group(1)
    if v.startswith("#"):
        return None, int(v[1:])
    return round(float(v) * 100), None


def _parse_time_control(tc: str) -> tuple[int | None, int | None]:
    if not tc or tc in ("-", "?"):
        return None, None
    m = re.match(r"(\d+)(?:\+(\d+))?", tc)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2) or 0)


def _int_or_none(s: str | None) -> int | None:
    if not s or s == "?":
        return None
    try:
        return int(str(s).lstrip("+"))
    except (ValueError, TypeError):
        return None


def _clean_comment(comment: str) -> str | None:
    """Strip [%clk ...] / [%eval ...] annotations; return remaining human text or None."""
    s = _ANN_RE.sub("", comment).strip()
    return s or None


def _game_to_row(game: chess.pgn.Game) -> dict:
    h = game.headers

    site    = h.get("Site") or ""
    game_id = site.rstrip("/").split("/")[-1] if site else None

    utc_date = h.get("UTCDate") or h.get("Date") or "?"
    utc_time = h.get("UTCTime") or "00:00:00"
    played_at = None
    if "?" not in utc_date:
        try:
            played_at = datetime.strptime(
                f"{utc_date} {utc_time}", "%Y.%m.%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    tc = h.get("TimeControl") or ""
    init_secs, inc_secs = _parse_time_control(tc)
    initial_fen = h.get("FEN") if h.get("SetUp") == "1" else None

    board = game.board()
    moves: list[dict] = []
    prev_clk: dict[str, int | None] = {"w": init_secs, "b": init_secs}
    inc = inc_secs or 0

    node = game
    while node.variations:
        node  = node.variations[0]
        color = "w" if board.turn == chess.WHITE else "b"
        san   = board.san(node.move)
        uci   = node.move.uci()
        board.push(node.move)

        comment  = node.comment or ""
        clk      = _parse_clk(comment)
        eval_cp, eval_mate = _parse_eval(comment)

        time_spent = None
        if clk is not None and prev_clk[color] is not None:
            time_spent = prev_clk[color] - clk + inc
        prev_clk[color] = clk

        nags = [_NAG_SYMBOLS.get(n, n) for n in sorted(node.nags)] if node.nags else None

        moves.append({
            "n":          node.ply(),
            "color":      color,
            "san":        san,
            "uci":        uci,
            "fen":        board.fen(),
            "clk":        clk,
            "time_spent": time_spent,
            "eval_cp":    eval_cp,
            "eval_mate":  eval_mate,
            "nags":       nags,
            "comment":    _clean_comment(comment),
        })

    return {
        "game_id":           game_id,
        "variant":           (h.get("Variant") or "standard").lower(),
        "event":             h.get("Event") or None,
        "site":              site or None,
        "white_username":    h.get("White") or None,
        "black_username":    h.get("Black") or None,
        "white_elo":         _int_or_none(h.get("WhiteElo")),
        "black_elo":         _int_or_none(h.get("BlackElo")),
        "white_rating_diff": _int_or_none(h.get("WhiteRatingDiff")),
        "black_rating_diff": _int_or_none(h.get("BlackRatingDiff")),
        "white_title":       h.get("WhiteTitle") or None,
        "black_title":       h.get("BlackTitle") or None,
        "white_team":        h.get("WhiteTeam") or None,
        "black_team":        h.get("BlackTeam") or None,
        "result":            h.get("Result") or None,
        "termination":       h.get("Termination") or None,
        "played_at":         played_at,
        "time_control":      tc or None,
        "initial_time_secs": init_secs,
        "increment_secs":    inc_secs,
        "eco":               h.get("ECO") or None,
        "opening":           h.get("Opening") or None,
        "initial_fen":       initial_fen,
        "ply_count":         len(moves),
        "pgn":               str(game),
        "moves":             json.dumps(moves, separators=(",", ":")),
    }


def _batch_to_table(batch: list[dict]) -> pa.Table:
    df = pd.DataFrame(batch)
    for col in ("white_elo", "black_elo", "white_rating_diff", "black_rating_diff",
                "ply_count", "increment_secs"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int16")
    df["initial_time_secs"] = pd.to_numeric(df["initial_time_secs"], errors="coerce").astype("Int32")
    df["played_at"] = pd.to_datetime(df["played_at"], utc=True, errors="coerce")
    return pa.Table.from_pandas(df, schema=_SCHEMA, safe=False)


def _open_pgn(path: Path):
    """Return a UTF-8 text stream from a .pgn or .pgn.zst file."""
    if path.suffix == ".zst":
        raw = open(path, "rb")
        dctx = zstd.ZstdDecompressor()
        return io.TextIOWrapper(
            io.BufferedReader(dctx.stream_reader(raw, closefd=True)),
            encoding="utf-8", errors="replace",
        )
    return open(path, encoding="utf-8", errors="replace")


def _iter_game_texts(f) -> Iterator[str]:
    """Yield the raw PGN text for each game."""
    buf: list[str] = []
    for line in f:
        if line.startswith("[Event ") and buf:
            yield "".join(buf)
            buf = []
        buf.append(line)
    if buf:
        yield "".join(buf)


def _parse_game_text(text: str) -> dict | None:
    """Parse one PGN game text into a row dict. Top-level so multiprocessing can pickle it."""
    try:
        game = chess.pgn.read_game(io.StringIO(text))
        if game is None:
            return None
        return _game_to_row(game)
    except Exception:
        return None


def parse_and_upload(
    api: HfApi,
    repo_id: str,
    pgn_path: Path,
    shard_size: int = DEFAULT_SHARD,
    workers: int | None = None,
    shard_dir: Path | None = None,
) -> None:
    """Stream-parse pgn_path (.pgn or .pgn.zst), write all Parquet shards locally,
    then push everything to HF in a single upload_folder call (one commit)."""
    m_v = _VARIANT_RE.search(pgn_path.name)
    m_m = _MONTH_RE.search(pgn_path.name)
    variant = m_v.group(1) if m_v else "unknown"
    month   = m_m.group(1) if m_m else pgn_path.stem
    prefix  = f"data/{variant}/{month}"

    managed = shard_dir is None
    if not managed:
        shard_dir.mkdir(parents=True, exist_ok=True)

    ctx = tempfile.TemporaryDirectory() if managed else contextlib.nullcontext(str(shard_dir))

    with ctx as tmpdir:
        out_dir   = Path(tmpdir)
        shard_idx = 0
        batch: list[dict] = []

        def _flush() -> None:
            nonlocal shard_idx
            if not batch:
                return
            table = _batch_to_table(batch)
            pq.write_table(
                table,
                out_dir / f"part-{shard_idx:04d}.parquet",
                compression="zstd",
            )
            tqdm.write(f"  wrote shard {shard_idx:04d} ({len(batch):,} games)")
            shard_idx += 1
            batch.clear()

        n_workers = workers if workers is not None else (os.cpu_count() or 4)
        with _open_pgn(pgn_path) as f, Pool(processes=n_workers) as pool:
            with tqdm(desc=pgn_path.name, unit=" games") as pbar:
                for row in pool.imap_unordered(
                    _parse_game_text, _iter_game_texts(f), chunksize=128
                ):
                    if row is not None:
                        batch.append(row)
                    pbar.update(1)
                    if len(batch) >= shard_size:
                        _flush()
            _flush()

        # Drop the .done marker into the same dir so it's part of the single commit
        (out_dir / ".done").write_text(f"{shard_idx} shards")

        print(f"  uploading {shard_idx} shard(s) in one commit …", flush=True)
        api.upload_folder(
            folder_path=str(out_dir),
            path_in_repo=prefix,
            repo_id=repo_id,
            repo_type="dataset",
            commit_message=f"Add {variant}/{month} — {shard_idx} shard(s)",
        )

    print(f"  complete: {variant}/{month} — {shard_idx} shard(s)", flush=True)


if __name__ == "__main__":
    import argparse
    token = os.getenv("HF_TOKEN")
    if not token:
        print("HF_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    p = argparse.ArgumentParser(description="Parse a .pgn or .pgn.zst and upload Parquet shards to HuggingFace")
    p.add_argument("file",          help="Path to .pgn or .pgn.zst file")
    p.add_argument("--repo",        default=DEFAULT_REPO)
    p.add_argument("--shard-size",  type=int, default=DEFAULT_SHARD)
    p.add_argument("--workers",     type=int, default=None,
                   help="Parser worker processes (default: all CPU cores)")
    p.add_argument("--shard-dir",   default=None,
                   help="Directory to write Parquet shards before uploading (default: system temp)")
    a = p.parse_args()
    parse_and_upload(
        HfApi(token=token), a.repo, Path(a.file),
        a.shard_size, a.workers,
        Path(a.shard_dir) if a.shard_dir else None,
    )
