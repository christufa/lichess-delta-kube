import io

import pyarrow.parquet as pq
import pytest

from pgn_timing import cli
from pgn_timing.parse import chunk_to_table, iter_chunks, parse_games

EDGE_CASES = """[Event "Rated Blitz game"]
[Site "https://lichess.org/abc12345"]
[White "quote\\"name"]
[Black "back\\\\slash"]
[Result "1-0"]
[CustomTag "x"]

1. e4 e5 2. Nf3
Nc6 1-0

[Event "Casual game"]
[Site "https://lichess.org/def67890"]
[Result "*"]

*

"""


def test_parse_games_handles_escapes_unknown_tags_and_wrapped_movetext():
    games = list(parse_games(EDGE_CASES))
    assert len(games) == 2
    tags, moves = games[0]
    assert tags["White"] == 'quote"name'
    assert tags["Black"] == "back\\slash"
    assert tags["CustomTag"] == "x"
    assert moves == "1. e4 e5 2. Nf3 Nc6 1-0"
    assert games[1][0]["Result"] == "*" and games[1][1] == "*"


def test_chunk_to_table_puts_unknown_tags_in_extra_tags():
    table, unknown = chunk_to_table(EDGE_CASES.encode())
    assert table.num_rows == 2
    assert table.column("extra_tags").to_pylist()[0] == [("CustomTag", "x")]
    assert table.column("Opening").to_pylist() == [None, None]
    assert unknown == {"CustomTag": 1}


@pytest.mark.parametrize("target", [1, 100, 5000, 1 << 20])
def test_chunks_never_split_a_game(target):
    data = EDGE_CASES.encode() * 50
    chunks = list(iter_chunks(io.BytesIO(data), target, read_size=37))
    assert b"".join(chunks) == data
    assert sum(chunk_to_table(c)[0].num_rows for c in chunks) == 100
    assert all(c.startswith(b"[") for c in chunks)


def test_parse_command_end_to_end(sample_pgn_zst, tmp_path):
    src, n = sample_pgn_zst
    out = tmp_path / "parquet"
    result = cli.main(["parse", str(src), "--workers", "2", "--chunk-mb", "1", "--out", str(out)])
    assert result["games"] == n
    files = sorted(out.glob("*.parquet"))
    assert len(files) == result["files"] > 1
    assert sum(pq.ParquetFile(f).metadata.num_rows for f in files) == n
    sites = {s for f in files for s in pq.read_table(f, columns=["Site"]).column("Site").to_pylist()}
    assert len(sites) == n
    assert result["unknown_tags"] == {}


def test_limit_stops_early(sample_pgn_zst):
    src, n = sample_pgn_zst
    result = cli.main(["parse", str(src), "--workers", "1", "--chunk-mb", "1", "--limit-mb", "1"])
    assert 0 < result["games"] < n


def test_decompress_and_commit(sample_pgn_zst, tmp_path):
    pytest.importorskip("pyiceberg")
    src, n = sample_pgn_zst
    assert cli.main(["decompress", str(src)])["decompressed_bytes"] > 0
    out = tmp_path / "parquet"
    cli.main(["parse", str(src), "--workers", "2", "--chunk-mb", "1", "--out", str(out)])
    result = cli.main(["commit", str(out), "--warehouse", str(tmp_path / "wh")])
    assert result["rows"] == n
    assert result["snapshot_added_records"] == str(n)
