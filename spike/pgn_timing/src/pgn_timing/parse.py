"""Split decompressed PGN into game-aligned chunks and parse headers into Arrow.

No chess logic: header tags become string columns and movetext stays one string.

Two parsers produce identical tables:
- `chunk_to_table_fast`: finds line boundaries with NumPy on the raw bytes and does
  the string work with Arrow compute kernels (no per-game Python loop).
- `chunk_to_table_reference`: straightforward pure-Python parser. Used as the
  fallback for unusual input and to check the fast parser on real data.
"""
from __future__ import annotations

import io
import os
import time
from collections import Counter

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .log import self_usage

# Tags Lichess writes for standard games; anything else goes to `extra_tags`.
KNOWN_TAGS = (
    "Event", "Site", "Date", "Round", "White", "Black", "Result",
    "UTCDate", "UTCTime", "WhiteElo", "BlackElo", "WhiteRatingDiff", "BlackRatingDiff",
    "WhiteTitle", "BlackTitle", "ECO", "Opening", "TimeControl", "Termination",
    "Variant", "FEN", "SetUp",
)
_KNOWN = set(KNOWN_TAGS)
EXTRA_TYPE = pa.map_(pa.string(), pa.string())

SCHEMA = pa.schema(
    [pa.field(t, pa.string()) for t in KNOWN_TAGS]
    + [pa.field("movetext", pa.string()), pa.field("extra_tags", EXTRA_TYPE)]
)

# A blank line followed by a tag line only occurs where a new game starts.
GAME_BOUNDARY = b"\n\n["


# --------------------------------------------------------------------------- chunking

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


# --------------------------------------------------------------------------- reference parser

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


def chunk_to_table_reference(chunk: bytes) -> tuple[pa.Table, Counter]:
    games = list(parse_games(chunk.decode("utf-8", errors="replace")))
    unknown = Counter()
    extra = []
    for tags, _ in games:
        others = [(k, v) for k, v in tags.items() if k not in _KNOWN]
        if others:
            unknown.update(k for k, _ in others)
        extra.append(others or None)
    arrays = [pa.array([g[0].get(t) for g in games], pa.string()) for t in KNOWN_TAGS]
    arrays += [pa.array([g[1] for g in games], pa.string()), pa.array(extra, EXTRA_TYPE)]
    return pa.Table.from_arrays(arrays, schema=SCHEMA), unknown


# --------------------------------------------------------------------------- fast parser

class Unsupported(Exception):
    """Input the fast parser does not handle; use the reference parser instead."""


def chunk_to_table_fast(chunk: bytes) -> tuple[pa.Table, Counter]:
    data = np.frombuffer(chunk, np.uint8)
    nl = np.flatnonzero(data == 10)
    ends_with_nl = len(nl) > 0 and nl[-1] == len(data) - 1
    offsets = np.concatenate(([0], nl + 1, [] if ends_with_nl else [len(data)])).astype(np.int64)
    if offsets[-1] >= 2**31:
        raise Unsupported("chunk larger than 2 GiB")
    offsets = offsets.astype(np.int32)
    lines = pa.Array.from_buffers(pa.string(), len(offsets) - 1, [None, pa.py_buffer(offsets), pa.py_buffer(chunk)])
    try:
        lines.validate(full=True)
    except pa.ArrowInvalid as exc:
        raise Unsupported(f"invalid UTF-8: {exc}") from None

    # Line geometry (content excludes the trailing newline).
    starts = offsets[:-1].astype(np.int64)
    cend = offsets[1:].astype(np.int64)
    cend[: len(nl)] -= 1
    length = cend - starts
    nonempty = length > 0
    first = np.zeros(len(starts), np.uint8)
    last = np.zeros(len(starts), np.uint8)
    ne = np.flatnonzero(nonempty)
    first[ne] = data[starts[ne]]
    last[ne] = data[cend[ne] - 1]

    # Blocks = runs of non-empty lines (same as splitting on blank lines).
    prev_nonempty = np.concatenate(([False], nonempty[:-1]))
    block_start = nonempty & ~prev_nonempty
    block_of_line = np.cumsum(block_start) - 1
    header_block = first[block_start] == ord("[")          # per block
    game_of_block = np.cumsum(header_block) - 1             # header blocks start games
    n_games = int(header_block.sum())

    in_block = nonempty
    blk = block_of_line[in_block]
    line_idx = np.flatnonzero(in_block)
    line_game = game_of_block[blk]
    line_in_hdr = header_block[blk]

    tag_shape = (length[line_idx] > 2) & (first[line_idx] == ord("[")) & (last[line_idx] == ord("]"))
    tag_sel = line_in_hdr & tag_shape & (line_game >= 0)
    mv_sel = ~line_in_hdr & (line_game >= 0)
    tag_lines, tag_game = line_idx[tag_sel], line_game[tag_sel]
    mv_lines, mv_game = line_idx[mv_sel], line_game[mv_sel]
    if len(np.unique(mv_game)) != len(mv_game):
        raise Unsupported("movetext spans multiple lines")

    hl = pc.utf8_rtrim(lines.take(pa.array(tag_lines)), characters="\n")
    parts = pc.split_pattern(pc.utf8_slice_codeunits(hl, 1, -1), " ", max_splits=1)
    if len(parts) and pc.min(pc.list_value_length(parts)).as_py() != 2:
        raise Unsupported("tag line without a value")
    keys = pc.list_element(parts, 0)
    vals = pc.list_element(parts, 1)
    quoted = pc.and_(pc.and_(pc.starts_with(vals, '"'), pc.ends_with(vals, '"')),
                     pc.greater_equal(pc.utf8_length(vals), 2))
    vals = pc.if_else(quoted, pc.utf8_slice_codeunits(vals, 1, -1), vals)
    if len(vals) and pc.any(pc.match_substring(vals, "\\")).as_py():
        vals = pc.replace_substring(pc.replace_substring(vals, '\\"', '"'), "\\\\", "\\")

    enc = pc.dictionary_encode(keys)
    codes = enc.indices.to_numpy(zero_copy_only=False)
    names = enc.dictionary.to_pylist()
    code_of = {name: i for i, name in enumerate(names)}

    def scatter(positions, games, source):
        target = np.full(n_games, -1, dtype=np.int64)
        target[games] = positions  # duplicate tags: last one wins, like dict assignment
        return source.take(pa.array(target, mask=target < 0))

    columns = []
    for tag in KNOWN_TAGS:
        code = code_of.get(tag)
        if code is None:
            columns.append(pa.nulls(n_games, pa.string()))
        else:
            pos = np.flatnonzero(codes == code)
            columns.append(scatter(pos, tag_game[pos], vals))

    moves = pc.utf8_rtrim(lines.take(pa.array(mv_lines)), characters="\n")
    columns.append(pc.fill_null(scatter(np.arange(len(mv_game)), mv_game, moves), ""))

    unknown = Counter()
    extra: list = [None] * n_games
    unknown_codes = [i for i, name in enumerate(names) if name not in _KNOWN]
    if unknown_codes:
        for p in np.flatnonzero(np.isin(codes, unknown_codes)):
            g, key = int(tag_game[p]), names[codes[p]]
            if extra[g] is None:
                extra[g] = {}
            extra[g][key] = vals[int(p)].as_py()  # dict: duplicate tags keep the last value
        for d in extra:
            if d:
                unknown.update(d.keys())  # count games with the tag, like the reference parser
        extra = [list(d.items()) if d else None for d in extra]
    columns.append(pa.array(extra, EXTRA_TYPE))
    return pa.Table.from_arrays(columns, schema=SCHEMA), unknown


def chunk_to_table(chunk: bytes) -> tuple[pa.Table, Counter, bool]:
    """Parse with the fast parser, falling back to the reference one. Returns (table, unknown, used_fallback)."""
    try:
        table, unknown = chunk_to_table_fast(chunk)
        return table, unknown, False
    except Unsupported:
        table, unknown = chunk_to_table_reference(chunk)
        return table, unknown, True


# --------------------------------------------------------------------------- worker

def process_chunk(seq: int, chunk: bytes, out_dir: str | None, compression: str = "zstd",
                  check_parity: bool = False) -> dict:
    """Worker entry point: parse one chunk and optionally write it as a Parquet file."""
    t0 = time.perf_counter()
    table, unknown, fallback = chunk_to_table(chunk)
    parse_s = time.perf_counter() - t0

    parity = None
    if check_parity:
        reference, _ = chunk_to_table_reference(chunk)
        parity = reference.equals(table)

    t1 = time.perf_counter()
    path = None
    if out_dir:
        path = os.path.join(out_dir, f"part-{seq:05d}.parquet")
        tmp = path + ".tmp"
        pq.write_table(table, tmp, compression=compression)
        os.replace(tmp, path)  # never leave a half-written file under the final name
        out_bytes = os.path.getsize(path)
    else:
        sink = io.BytesIO()  # still pay the encode cost so timings are comparable
        pq.write_table(table, sink, compression=compression)
        out_bytes = sink.tell()
    write_s = time.perf_counter() - t1

    return {"seq": seq, "games": table.num_rows, "in_bytes": len(chunk), "out_bytes": out_bytes,
            "parse_s": parse_s, "write_s": write_s, "unknown_tags": dict(unknown),
            "fallback": fallback, "parity": parity,
            "file": os.path.basename(path) if path else None,
            "pid": os.getpid(), "usage": self_usage()}
