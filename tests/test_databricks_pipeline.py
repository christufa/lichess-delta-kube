import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lichess_pipeline import common
from lichess_pipeline import download
from lichess_pipeline import extract
from lichess_pipeline import insert
from lichess_pipeline import upload


def task_args(**overrides):
    return SimpleNamespace(**dict(dict(year=2024, month=1, variant="standard", limit=1,
                                      full_table="`brikt`.`lichess_dev`.`games`",
                                      repo="owner/dataset", secret_scope="lichess",
                                      secret_key="hf-token", shard_size=200000), **overrides))

PGN = '''[Event "Rated Blitz game"]
[Site "https://lichess.org/abcd1234"]
[White "Alice"]
[Black "Bob"]
[Result "1-0"]
[UTCDate "2024.01.01"]
[UTCTime "12:00:00"]
[WhiteElo "1500"]
[TimeControl "180+2"]

1. e4 { [%clk 0:03:00] [%eval 0.18] } e5 2. Nf3 Nc6 1-0
'''


def test_staging_preserves_complete_games_and_unicode(tmp_path):
    pgn = PGN.replace("Alice", "AlÃ­ce")
    assert extract.stage_games(io.StringIO(pgn + "\n" + PGN), tmp_path, 1) == 2
    files = sorted(tmp_path.glob("*.jsonl"))
    assert len(files) == 2
    assert json.loads(files[0].read_text(encoding="utf-8"))["pgn"] == pgn.strip()
    assert json.loads(files[1].read_text(encoding="utf-8"))["pgn"] == PGN.strip()


def test_parse_preserves_schema_and_move_annotations():
    frame = next(insert.parse_batches(iter([pd.DataFrame({"pgn": [PGN]})])))
    row = frame.iloc[0]
    assert row.game_id == "abcd1234"
    assert row.white_elo == 1500
    assert pd.isna(row.black_elo)
    assert row.played_at.isoformat() == "2024-01-01T12:00:00+00:00"
    moves = json.loads(row.moves)
    assert len(moves) == 4
    assert moves[0]["eval_cp"] == 18
    assert moves[0]["clk"] == 180
    assert moves[0]["san"] == "e4"


def test_bad_game_fails_instead_of_silently_dropping_rows():
    with pytest.raises(ValueError, match="Invalid PGN"):
        list(insert.parse_batches(iter([pd.DataFrame({"pgn": [PGN.replace("1. e4", "1. e5")]})])))


def test_empty_archive_fails(tmp_path):
    with pytest.raises(ValueError, match="no games"):
        extract.stage_games(io.StringIO("\n"), tmp_path)


def test_period_requires_both_values():
    assert common.selected_period(2024, 1) == (2024, 1)
    with pytest.raises(ValueError):
        common.selected_period(2024, 0)


def test_range_ignored_restarts_download(tmp_path):
    dl = download
    dest = tmp_path / "test.zst"
    dest.with_suffix(".zst.part").write_bytes(b"old")
    class Response:
        status_code = 200
        headers = {"content-length": "3"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, chunk): return iter([b"new"])
    with patch.object(dl.requests, "get", return_value=Response()):
        dl.download_file("https://example.com/test.zst", dest)
    assert dest.read_bytes() == b"new"


def test_download_extract_handoff_and_retry(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import zstandard

    root = tmp_path / "runs" / "123"
    name = "lichess_db_standard_rated_2024-01.pgn.zst"
    payload = zstandard.ZstdCompressor().compress((PGN + "\n" + PGN).encode())
    calls = []
    monkeypatch.setattr(download, "fetch_links", lambda *args: [(name, "https://example.com/archive")])
    def fake_download(url, path):
        calls.append(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    monkeypatch.setattr(download, "download_file", fake_download)
    args = task_args()

    download.download_archives(args, None, root)
    manifest = common.read_json(root / "downloads.json")
    assert manifest[0]["month"] == "2024-01"
    assert not (root / "archives.json").exists()
    download.download_archives(args, None, root)
    assert len(calls) == 1  # Download retries reuse the completed archive.

    extract.extract_archives(args, None, root)
    archive = common.read_json(root / "archives.json")[0]
    assert archive["games"] == 2
    records = [json.loads(line) for file in Path(archive["path"]).glob("*.jsonl")
               for line in file.read_text(encoding="utf-8").splitlines()]
    frames = list(insert.parse_batches(iter([pd.DataFrame(records)])))
    assert len(frames[0]) == 2
    assert len(calls) == 1  # Extraction never downloads.


def test_failed_extraction_does_not_publish_manifest(tmp_path):
    import zstandard
    raw = tmp_path / "bad.zst"
    raw.write_bytes(b"not a zstandard archive")
    root = tmp_path / "runs" / "123"
    common.write_json(root / "downloads.json", [
        {"variant": "standard", "month": "2024-01", "raw_path": str(raw)}
    ])
    with pytest.raises(zstandard.ZstdError):
        extract.extract_archives(task_args(), None, root)
    assert not (root / "archives.json").exists()


def test_bundle_task_dependencies_and_entrypoints():
    import yaml
    repo = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((repo / "resources/lichess_job.yml").read_text())
    tasks = config["resources"]["jobs"]["lichess_pipeline"]["tasks"]
    assert [task["task_key"] for task in tasks] == ["download", "extract", "insert", "upload"]
    previous = None
    for task in tasks:
        assert task.get("depends_on", []) == ([{"task_key": previous}] if previous else [])
        assert task["python_wheel_task"]["package_name"] == "lichess_pipeline"
        assert task["python_wheel_task"]["entry_point"] == task["task_key"]
        params = task["python_wheel_task"]["parameters"]
        assert params[params.index("--run-id") + 1] == "{{job.run_id}}"
        previous = task["task_key"]



def test_parser_bounds_output_batches():
    frames = list(insert.parse_batches(iter([pd.DataFrame({"pgn": [PGN] * 300})])))
    assert [len(frame) for frame in frames] == [256, 44]



def test_completed_month_skips_network_and_all_stages(tmp_path, monkeypatch):
    args = task_args()
    old_root = tmp_path / "runs" / "old"
    archive = {"variant": "standard", "month": "2024-01", "version": 7}
    common.record_completion(old_root, args, archive, "upload")
    root = tmp_path / "runs" / "new"
    def unexpected(*args, **kwargs):
        raise AssertionError("A completed month must not perform network I/O")
    monkeypatch.setattr(download, "fetch_links", unexpected)
    download.download_archives(args, None, root)
    extract.extract_archives(args, None, root)
    insert.ingest(args, None, root)
    upload.upload(args, None, root)
    assert common.read_json(root / "delta.json") == []


def test_new_run_resumes_committed_delta_without_source_files(tmp_path, monkeypatch):
    args = task_args()
    old_root = tmp_path / "runs" / "old"
    archive = {"variant": "standard", "month": "2024-01", "version": 7, "games": 2}
    common.record_completion(old_root, args, archive, "insert")
    root = tmp_path / "runs" / "new"
    monkeypatch.setattr(download, "fetch_links", lambda *args: pytest.fail("Should reuse Delta"))
    download.download_archives(args, None, root)
    extract.extract_archives(args, None, root)
    insert.ingest(args, None, root)
    assert common.read_json(root / "delta.json") == [archive]


def test_history_is_scoped_to_table_and_repo(tmp_path):
    root = tmp_path / "runs" / "old"
    archive = {"variant": "standard", "month": "2024-01"}
    common.record_completion(root, task_args(), archive, "upload")
    assert common.checkpoint(root, task_args(repo="owner/another"), archive, "upload") is None
    assert common.checkpoint(root, task_args(full_table="another.table"), archive, "upload") is None
    record = common.read_json(common.history_directory(root, task_args(), archive) / "upload.json")
    assert record["run_id"] == "old"
    assert record["completed_at"]


def test_extraction_checkpoint_reused_by_new_run(tmp_path, monkeypatch):
    args = task_args()
    archive = {"variant": "standard", "month": "2024-01", "games": 2, "path": "cached"}
    common.record_completion(tmp_path / "runs" / "old", args, archive, "extract")
    root = tmp_path / "runs" / "new"
    monkeypatch.setattr(download, "fetch_links", lambda *args: pytest.fail("Should reuse extraction"))
    download.download_archives(args, None, root)
    extract.extract_archives(args, None, root)
    assert common.read_json(root / "archives.json") == [archive]


@pytest.mark.parametrize("fail", [True, False])
def test_upload_completion_is_only_recorded_after_commit(tmp_path, monkeypatch, fail):
    args = task_args()
    root = tmp_path / "runs" / "123"
    archive = {"variant": "standard", "month": "2024-01", "version": 7, "games": 2}
    common.write_json(root / "delta.json", [archive])
    class Api:
        def __init__(self, **kwargs): pass
        def create_repo(self, *args, **kwargs): pass
        def upload_folder(self, **kwargs):
            assert common.checkpoint(root, args, archive, "upload") is None
            if fail:
                raise RuntimeError("Upload failed")
            return SimpleNamespace(oid="commit123")
    class Frame:
        @property
        def read(self): return self
        @property
        def write(self): return self
        def option(self, *args): return self
        def table(self, *args): return self
        def where(self, *args): return self
        def select(self, *args): return self
        def mode(self, *args): return self
        def parquet(self, directory):
            Path(directory).mkdir(parents=True)
            (Path(directory) / "part-0000.parquet").write_bytes(b"mock")
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(HfApi=Api))
    monkeypatch.setitem(sys.modules, "pyspark", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pyspark.dbutils", SimpleNamespace(
        DBUtils=lambda spark: SimpleNamespace(secrets=SimpleNamespace(get=lambda **kwargs: "test"))))
    if fail:
        with pytest.raises(RuntimeError, match="Upload failed"):
            upload.upload(args, Frame(), root)
        assert common.checkpoint(root, args, archive, "upload") is None
    else:
        upload.upload(args, Frame(), root)
        assert common.checkpoint(root, args, archive, "upload")["hf_commit"] == "commit123"
