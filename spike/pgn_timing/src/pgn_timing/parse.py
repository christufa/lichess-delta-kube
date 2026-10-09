"""Split decompressed PGN into game-aligned chunks and parse headers into Arrow.

No chess logic: header tags become string columns and movetext stays one string.
"""
from __future__ import annotations

import io
import time
from collections import Counter

import pyarrow as pa
import pyarrow.parquet as pq

# Tags Lichess writes for standard games; anything else goes to `extra_tags`.
KNOWN_TAGS = (
    "Event", "Site", "Date", "Round", "White", "Black", "Result",
    "UTCDate", "UTCTime", "WhiteElo", "BlackElo", "WhiteRatingDiff", "BlackRatingDiff",
    "WhiteTitle", "BlackTitle", "ECO", "Opening", "TimeControl", "Termination",
    "Variant", "FEN", "SetUp",
)
_KNOWN = set(KNOWN_TAGS)

SCHEMA = pa.schema(
    [pa.field(t, pa.string()) for t in KNOWN_TAGS]
    + [pa.field("movetext", pa.string()),
       pa.field("extra_tags", pa.map_(pa.string(), pa.string()))]
)

# A blank line followed by a tag line only occurs where a new game starts.
GAME_BOUNDARY = b"\n\n["


def iter_chunks(reader, target_bytes: int, read_size: int = 8 << 20, timings: dict | None = None):
    """Yield byte chunks of roughly `target_bytes`, each ending just before a game starts.

    `reader` is any object with `.read(n)` returning decompressed bytes.
    """
    buf = bytearray()
    read_s = 0.0
    while True:
        t0 = time.perf_counter()
        block = reader.read(read_size)
        read_s += time.perf_counter() - t0
        if timings is not None:
            timings["read_s"] = read_s
        if not block:
            break
        buf += block
        if len(buf) < target_bytes:
            continue
        cut = buf.rfind(GAME_BOUNDARY)
        if cut <= 0:
            continue  # a single game larger than the target; keep reading
        yield bytes(buf[: cut + 2])
        del buf[: cut + 2]
    if getattr(reader, "truncated", False):
        # Input was cut at an arbitrary byte (--limit-mb): drop the partial last game.
        cut = buf.rfind(GAME_BOUNDARY)
        buf = buf[: cut + 2] if cut > 0 else bytearray()
    if buf.strip():
        yield bytes(buf)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    if "\\" in value:
        value = value.replace('\\"', '"').replace("\\\\", "\\")
    return value


def parse_games(text: str):
    """Yield (tags: dict, movetext: str) for each game in `text`."""
    tags: dict | None = None
    moves: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        if block[0] == "[":
            if tags is not None:
                yield tags, " ".join(moves)
            tags, moves = {}, []
            for line in block.split("\n"):
                if len(line) > 2 and line[0] == "[" and line[-1] == "]":
                    key, _, value = line[1:-1].partition(" ")
                    tags[key] = _unquote(value)
        elif tags is not None:
            moves.append(block.replace("\n", " "))
    if tags is not None:
        yield tags, " ".join(moves)


def chunk_to_table(chunk: bytes) -> tuple[pa.Table, Counter]:
    columns = {t: [] for t in KNOWN_TAGS}
    movetext, extra = [], []
    unknown = Counter()
    for tags, moves in parse_games(chunk.decode("utf-8", errors="replace")):
        for t in KNOWN_TAGS:
            columns[t].append(tags.get(t))
        movetext.append(moves)
        others = [(k, v) for k, v in tags.items() if k not in _KNOWN]
        if others:
            unknown.update(k for k, _ in others)
            extra.append(others)
        else:
            extra.append(None)
    arrays = [pa.array(columns[t], pa.string()) for t in KNOWN_TAGS]
    arrays += [pa.array(movetext, pa.string()), pa.array(extra, SCHEMA.field("extra_tags").type)]
    return pa.Table.from_arrays(arrays, schema=SCHEMA), unknown


def process_chunk(seq: int, chunk: bytes, out_dir: str | None, compression: str = "zstd") -> dict:
    """Worker entry point: parse one chunk and optionally write it as a Parquet file."""
    t0 = time.perf_counter()
    table, unknown = chunk_to_table(chunk)
    parse_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    if out_dir:
        path = f"{out_dir.rstrip('/')}/part-{seq:05d}.parquet"
        pq.write_table(table, path, compression=compression)
        out_bytes = _size(path)
    else:
        sink = io.BytesIO()  # still pay the encode cost so timings are comparable
        pq.write_table(table, sink, compression=compression)
        out_bytes = sink.tell()
    write_s = time.perf_counter() - t1

    return {"seq": seq, "games": table.num_rows, "in_bytes": len(chunk), "out_bytes": out_bytes,
            "parse_s": parse_s, "write_s": write_s, "unknown_tags": dict(unknown)}


def _size(path: str) -> int:
    import os
    return os.path.getsize(path)
