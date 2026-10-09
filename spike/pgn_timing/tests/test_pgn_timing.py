import hashlib
import io
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pyarrow.parquet as pq
import pytest

from pgn_timing import cli
from pgn_timing.parse import (Unsupported, chunk_to_table, chunk_to_table_fast,
                              chunk_to_table_reference, iter_chunks, parse_games)

EDGE_CASES = """[Event "Rated Blitz game"]
[Site "https://lichess.org/abc12345"]
[White "quote\\"name"]
[Black "back\\\\slash"]
[Result "1-0"]
[CustomTag "x"]

1. e4 e5 2. Nf3 Nc6 1-0

[Event "Casual game"]
[Site "https://lichess.org/def67890"]
[Result "*"]
[Opening "Unquoted value"]
[Weird unquoted]

*

[Event "No moves"]
[Site "https://lichess.org/nomoves1"]
[Result "*"]
[CustomTag "dup1"]
[CustomTag "dup2"]

"""


# --------------------------------------------------------------------------- parsing

def test_parse_games_handles_escapes_unknown_tags_and_wrapped_movetext():
    text = EDGE_CASES.replace("2. Nf3 Nc6", "2. Nf3\nNc6")
    games = list(parse_games(text))
    assert len(games) == 3
    tags, moves = games[0]
    assert tags["White"] == 'quote"name'
    assert tags["Black"] == "back\\slash"
    assert tags["CustomTag"] == "x"
    assert moves == "1. e4 e5 2. Nf3 Nc6 1-0"
    assert games[1][0]["Weird"] == "unquoted"
    assert games[2][1] == ""


def test_fast_parser_matches_reference_on_edge_cases():
    fast, unknown = chunk_to_table_fast(EDGE_CASES.encode())
    ref, ref_unknown = chunk_to_table_reference(EDGE_CASES.encode())
    assert fast.equals(ref)
    assert unknown == ref_unknown == {"CustomTag": 2, "Weird": 1}
    assert fast.column("extra_tags").to_pylist()[2] == [("CustomTag", "dup2")]
    assert fast.column("movetext").to_pylist()[2] == ""


def test_fast_parser_matches_reference_on_generated_games(sample_pgn_zst):
    import zstandard
    path, n = sample_pgn_zst
    data = zstandard.ZstdDecompressor().decompress(path.read_bytes())
    fast, _ = chunk_to_table_fast(data)
    ref, _ = chunk_to_table_reference(data)
    assert fast.num_rows == n and fast.equals(ref)


@pytest.mark.parametrize("bad", [
    EDGE_CASES.replace("2. Nf3 Nc6", "2. Nf3\nNc6"),          # movetext over two lines
    EDGE_CASES.replace("[Weird unquoted]", "[Novalue]"),       # tag line without a value
])
def test_unusual_input_falls_back_to_reference(bad):
    with pytest.raises(Unsupported):
        chunk_to_table_fast(bad.encode())
    table, _, fallback = chunk_to_table(bad.encode())
    assert fallback and table.equals(chunk_to_table_reference(bad.encode())[0])


def test_invalid_utf8_falls_back_and_is_replaced():
    bad = EDGE_CASES.encode().replace(b"quote", b"qu\xffote")
    table, _, fallback = chunk_to_table(bad)
    assert fallback
    assert table.column("White").to_pylist()[0] == 'qu�ote"name'


@pytest.mark.parametrize("target", [1, 100, 5000, 1 << 20])
def test_chunks_never_split_a_game(target):
    data = EDGE_CASES.encode() * 50
    chunks = list(iter_chunks(io.BytesIO(data), target, read_size=37))
    assert b"".join(chunks) == data
    assert sum(chunk_to_table(c)[0].num_rows for c in chunks) == 150
    assert all(c.startswith(b"[") for c in chunks)


# --------------------------------------------------------------------------- parse / commit commands

def test_parse_command_end_to_end(sample_pgn_zst, tmp_path, capsys):
    src, n = sample_pgn_zst
    out = tmp_path / "parquet"
    result = cli.main(["parse", str(src), "--workers", "2", "--chunk-mb", "1", "--out", str(out)])
    assert result["status"] == "ok" and result["games"] == n
    assert result["parity_check"] == {"checked": True, "equal": True}
    assert result["fallback_chunks"] == 0
    files = sorted(out.glob("*.parquet"))
    assert len(files) == result["files"] > 1
    assert not list(out.glob("*.tmp"))
    assert sum(pq.ParquetFile(f).metadata.num_rows for f in files) == n
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["games"] == n and len(manifest["files"]) == len(files)
    sites = {s for f in files for s in pq.read_table(f, columns=["Site"]).column("Site").to_pylist()}
    assert len(sites) == n
    assert "resources" in result
    # stdout carries exactly one JSON document
    assert json.loads(capsys.readouterr().out)["games"] == n


def test_parse_refuses_to_mix_with_previous_output(sample_pgn_zst, tmp_path):
    src, n = sample_pgn_zst
    out = tmp_path / "parquet"
    cli.main(["parse", str(src), "--workers", "1", "--chunk-mb", "1", "--out", str(out)])
    report = tmp_path / "runs.jsonl"
    with pytest.raises(SystemExit) as exc:
        cli.main(["--report", str(report), "parse", str(src), "--workers", "1", "--out", str(out)])
    assert exc.value.code == 1
    failed = json.loads(report.read_text().splitlines()[-1])
    assert failed["status"] == "failed" and "FileExistsError" in failed["error"]
    again = cli.main(["parse", str(src), "--workers", "1", "--chunk-mb", "1", "--out", str(out), "--overwrite"])
    assert again["games"] == n


def test_limit_stops_early(sample_pgn_zst):
    src, n = sample_pgn_zst
    result = cli.main(["parse", str(src), "--workers", "1", "--chunk-mb", "1", "--limit-mb", "1"])
    assert 0 < result["games"] < n


def test_corrupt_input_is_recorded_as_failed(tmp_path):
    bad = tmp_path / "bad.pgn.zst"
    bad.write_bytes(b"this is not zstd" * 100)
    report = tmp_path / "runs.jsonl"
    with pytest.raises(SystemExit) as exc:
        cli.main(["--report", str(report), "parse", str(bad), "--workers", "1"])
    assert exc.value.code == 1
    record = json.loads(report.read_text())
    assert record["status"] == "failed" and record["stage"] == "parse" and record["error"]


def test_decompress_and_commit(sample_pgn_zst, tmp_path):
    pytest.importorskip("pyiceberg")
    src, n = sample_pgn_zst
    dec = cli.main(["decompress", str(src)])
    assert dec["decompressed_bytes"] > 0 and dec["compression_ratio"] > 1
    out = tmp_path / "parquet"
    cli.main(["parse", str(src), "--workers", "2", "--chunk-mb", "1", "--out", str(out)])
    result = cli.main(["commit", str(out), "--warehouse", str(tmp_path / "wh")])
    assert result["rows"] == n and result["snapshot_added_records"] == n


def test_commit_detects_missing_files(sample_pgn_zst, tmp_path):
    pytest.importorskip("pyiceberg")
    src, _ = sample_pgn_zst
    out = tmp_path / "parquet"
    cli.main(["parse", str(src), "--workers", "1", "--chunk-mb", "1", "--out", str(out)])
    sorted(out.glob("*.parquet"))[0].unlink()
    with pytest.raises(SystemExit):
        cli.main(["commit", str(out), "--warehouse", str(tmp_path / "wh")])


def test_pychess_baseline(sample_pgn_zst):
    pytest.importorskip("chess")
    src, _ = sample_pgn_zst
    result = cli.main(["pychess", str(src), "--games", "200"])
    assert result["games"] == 200 and result["games_per_s"] > 0


# --------------------------------------------------------------------------- download

class RangeHandler(SimpleHTTPRequestHandler):
    """Static file server with single-range support, optionally cutting the first response short."""

    cut_first_response_at: int | None = None
    served = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.translate_path(self.path)
        try:
            data = open(path, "rb").read()
        except OSError:
            self.send_error(404)
            return
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(data):
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        type(self).served += 1
        if type(self).cut_first_response_at and type(self).served == 1:
            self.wfile.write(body[: type(self).cut_first_response_at])
            self.wfile.flush()
            self.connection.shutdown(2)  # simulate a dropped connection
            return
        self.wfile.write(body)


@pytest.fixture
def http_dir(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    RangeHandler.cut_first_response_at = None
    RangeHandler.served = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(RangeHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _publish(root, name, payload, checksum=None):
    (root / name).write_bytes(payload)
    digest = checksum or hashlib.sha256(payload).hexdigest()
    (root / "sha256sums.txt").write_text(f"{digest}  {name}\n")
    return hashlib.sha256(payload).hexdigest()


def test_download_verifies_checksum_and_keeps_stdout_clean(http_dir, tmp_path, capsys):
    root, base = http_dir
    payload = bytes(range(256)) * 4000
    digest = _publish(root, "month.pgn.zst", payload)
    out = tmp_path / "dl" / "month.pgn.zst"
    result = cli.main(["download", f"{base}/month.pgn.zst", "--out", str(out)])
    assert out.read_bytes() == payload
    assert result["checksum"] == "ok" and result["sha256"] == digest
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_download_retries_and_resumes_after_dropped_connection(http_dir, tmp_path):
    root, base = http_dir
    payload = b"x" * 300_000
    _publish(root, "month.pgn.zst", payload)
    RangeHandler.cut_first_response_at = 100_000
    out = tmp_path / "month.pgn.zst"
    result = cli.main(["download", f"{base}/month.pgn.zst", "--out", str(out), "--retries", "2"])
    assert out.read_bytes() == payload
    assert result["retries"] == 1 and result["checksum"] == "ok"


def test_download_resumes_existing_partial_file(http_dir, tmp_path):
    root, base = http_dir
    payload = b"0123456789" * 50_000
    _publish(root, "month.pgn.zst", payload)
    out = tmp_path / "month.pgn.zst"
    out.write_bytes(payload[:123_456])
    result = cli.main(["download", f"{base}/month.pgn.zst", "--out", str(out)])
    assert out.read_bytes() == payload
    assert result["resumed_from_bytes"] == 123_456
    assert result["downloaded_bytes"] == len(payload) - 123_456


def test_download_checksum_mismatch_fails(http_dir, tmp_path):
    root, base = http_dir
    _publish(root, "month.pgn.zst", b"abc" * 1000, checksum="0" * 64)
    report = tmp_path / "runs.jsonl"
    with pytest.raises(SystemExit):
        cli.main(["--report", str(report), "download", f"{base}/month.pgn.zst", "--out", str(tmp_path / "m.zst")])
    record = json.loads(report.read_text())
    assert record["status"] == "failed" and record["checksum"] == "mismatch"


def test_download_without_published_checksums_still_succeeds(http_dir, tmp_path):
    root, base = http_dir
    (root / "month.pgn.zst").write_bytes(b"data" * 100)
    result = cli.main(["download", f"{base}/month.pgn.zst", "--out", str(tmp_path / "m.zst")])
    assert result["checksum"] == "unavailable"


def test_console_entry_exits_zero_and_prints_only_json(sample_pgn_zst):
    import subprocess
    import sys
    src, n = sample_pgn_zst
    proc = subprocess.run([sys.executable, "-m", "pgn_timing", "parse", str(src), "--workers", "1"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["games"] == n
    assert "parse complete" in proc.stderr


def test_console_entry_exits_nonzero_on_failure(tmp_path):
    import subprocess
    import sys
    proc = subprocess.run([sys.executable, "-m", "pgn_timing", "parse", str(tmp_path / "missing.pgn.zst")],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["status"] == "failed"
