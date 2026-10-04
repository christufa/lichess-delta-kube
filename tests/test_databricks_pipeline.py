import hashlib
import json
import sys
from datetime import date, time
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from huggingface_hub.hf_api import RepoFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lichess_pipeline import common, download, insert, sync


def args(**overrides):
    return SimpleNamespace(**dict(dict(year=0, month=0, limit=0, workers=2,
                                      full_table="`brikt`.`lichess_dev`.`games_hf`"), **overrides))


def archive(month="2024-01", fingerprint="abc"):
    return {"variant": "standard", "month": month, "fingerprint": fingerprint,
            "revision": "commit-a", "files": []}


def repo_file(month="01", index=0, total=1, oid="blob"):
    return RepoFile(path=f"data/year=2024/month={month}/train-{index:05d}-of-{total:05d}.parquet",
                    size=100, oid=oid)


def fake_api(monkeypatch, files, revision="commit-a"):
    class Api:
        def __init__(self, **kwargs): assert kwargs["token"] is False
        def dataset_info(self, repo): return SimpleNamespace(sha=revision)
        def list_repo_tree(self, repo, **kwargs):
            assert kwargs["revision"] == revision
            return iter(files)
    monkeypatch.setattr(download, "HfApi", Api)


def test_discovery_pins_revision_and_selects_month(monkeypatch):
    fake_api(monkeypatch, [repo_file("02"), repo_file("01")])
    months = download.discover_months()
    assert [a["month"] for a in months] == ["2024-01", "2024-02"]
    assert all(a["revision"] == "commit-a" for a in months)
    assert len(download.discover_months(2024, 2)) == 1
    with pytest.raises(ValueError, match="No published"):
        download.discover_months(2026, 9)


def test_fingerprint_changes_only_when_month_files_change(monkeypatch):
    fake_api(monkeypatch, [repo_file()])
    original = download.discover_months()[0]["fingerprint"]
    fake_api(monkeypatch, [repo_file()], revision="commit-b")
    assert download.discover_months()[0]["fingerprint"] == original
    fake_api(monkeypatch, [repo_file(oid="changed")])
    assert download.discover_months()[0]["fingerprint"] != original


@pytest.mark.parametrize("files", [[repo_file(total=2)], [repo_file(index=1)],
                                    [repo_file(total=2), repo_file(index=1, total=3)]])
def test_incomplete_shards_rejected(monkeypatch, files):
    fake_api(monkeypatch, files)
    with pytest.raises(ValueError, match="Incomplete"):
        download.discover_months()


def sample_table():
    return pa.table({"Site": ["https://lichess.org/abc", "https://lichess.org/xyz"],
                     "UTCDate": pa.array([date(2024, 1, 1), None], type=pa.date32()),
                     "UTCTime": pa.array([time(12, 34, 56, 123000), None], type=pa.time32("ms")),
                     "movetext": ["1. e4 { [%clk 0:03:00] } e5", "1. d4"],
                     "White": ["Alíce", "Bob"], "WhiteElo": pa.array([1500, None], type=pa.int16())})


def test_footer_schema_preserves_source_and_maps_time(tmp_path):
    source = tmp_path / "source.parquet"
    table = sample_table()
    pq.write_table(table, source)
    metadata = download.inspect_parquet(source)
    assert metadata["games"] == 2
    types = {f["name"]: f["type"] for f in metadata["read_schema"]["fields"]}
    assert types["UTCTime"] == "integer"
    assert types["WhiteElo"] == "short"
    assert types["UTCDate"] == "date"
    assert metadata["time_columns"] == ["UTCTime"]
    assert pq.read_table(source).equals(table)
    assert list(tmp_path.iterdir()) == [source]


def test_invalid_schema_fails_before_delta(tmp_path):
    source = tmp_path / "bad.parquet"
    pq.write_table(pa.table({"wrong": [1]}), source)
    with pytest.raises(ValueError, match="Missing HF"):
        download.inspect_parquet(source)
    table = sample_table().append_column("unsupported", pa.array([[1], [2]]))
    pq.write_table(table, source)
    with pytest.raises(ValueError, match="Unsupported HF"):
        download.inspect_parquet(source)


def test_stage_uses_pinned_public_source_and_all_files(tmp_path, monkeypatch):
    source = tmp_path / "source.parquet"
    pq.write_table(sample_table(), source)
    calls = []
    def fetch(repo, path, **kwargs):
        assert repo == common.SOURCE_REPO
        assert kwargs["revision"] == "commit-a" and kwargs["token"] is False
        calls.append(path)
        return str(source)
    monkeypatch.setattr(download, "hf_hub_download", fetch)
    a = archive()
    a["files"] = [{"path": f"data/year=2024/month=01/train-{i}.parquet",
                   "size": source.stat().st_size} for i in range(2)]
    staged = download.stage_month(a, args(), tmp_path / "runs" / "1")
    assert len(calls) == 2 and len(staged["paths"]) == 2 and staged["games"] == 4
    assert staged["paths"] == [str(source), str(source)]
    assert staged["time_columns"] == ["UTCTime"]
    assert not list(tmp_path.rglob("prepared"))


def test_checkpoint_ignores_old_pipeline_and_changed_source(tmp_path):
    root, a = tmp_path / "runs" / "1", archive()
    old_hash = hashlib.sha256(json.dumps([args().full_table]).encode()).hexdigest()[:24]
    common.write_json(tmp_path / "history" / old_hash / "standard" / "2024-01" / "insert.json", {})
    assert common.checkpoint(root, args(), a, "insert") is None
    common.record_completion(root, args(), a, "insert")
    assert common.checkpoint(root, args(), a, "insert") == a
    assert common.checkpoint(root, args(), archive(fingerprint="changed"), "insert") is None
    assert common.checkpoint(root, args(full_table="other.table"), a, "insert") is None


def test_sync_retry_skips_committed_month_and_keeps_plan(tmp_path, monkeypatch):
    root = tmp_path / "runs" / "1"
    months = [archive("2024-01"), archive("2024-02")]
    monkeypatch.setattr(sync, "discover_months", lambda *a: months)
    downloads = []
    def stage(a, *unused):
        downloads.append(a["month"])
        return {**a, "games": 2, "paths": ["file"]}
    monkeypatch.setattr(sync, "stage_month", stage)
    def ingest(a, *unused):
        if a["month"] == "2024-02": raise RuntimeError("Delta failed")
        return {**a, "version": 1}
    monkeypatch.setattr(sync, "ingest_month", ingest)
    with pytest.raises(RuntimeError, match="Delta failed"):
        sync.sync_dataset(args(), None, root)
    assert common.checkpoint(root, args(), months[0], "insert")
    assert common.checkpoint(root, args(), months[1], "insert") is None
    monkeypatch.setattr(sync, "discover_months", lambda *a: pytest.fail("Must use pinned plan"))
    monkeypatch.setattr(sync, "ingest_month", lambda a, *unused: {**a, "version": 2})
    sync.sync_dataset(args(), None, root)
    assert downloads == ["2024-01", "2024-02", "2024-02"]
    assert len(common.read_json(root / "delta.json")) == 2


def test_limit_applies_to_pending_months(tmp_path, monkeypatch):
    root = tmp_path / "runs" / "1"
    months = [archive("2024-01"), archive("2024-02"), archive("2024-03")]
    common.record_completion(root, args(), {**months[0], "games": 2}, "insert")
    monkeypatch.setattr(sync, "discover_months", lambda *a: months)
    monkeypatch.setattr(sync, "stage_month", lambda a, *unused: {**a, "games": 2})
    monkeypatch.setattr(sync, "ingest_month", lambda a, *unused: {**a, "version": 1})
    sync.sync_dataset(args(limit=1), None, root)
    assert [a["month"] for a in common.read_json(root / "delta.json")] == ["2024-02"]


@pytest.mark.parametrize("actual", [2, 1])
def test_delta_partition_replacement_and_count_verification(monkeypatch, actual):
    calls = []
    monkeypatch.setattr(insert, "read_source", lambda a, spark: spark.parquet(*a["paths"]))
    class Spark:
        @property
        def read(self): return self
        @property
        def write(self): return self
        def option(self, key, value): calls.append((key, value)); return self
        def parquet(self, *paths): calls.append(("paths", paths)); return self
        def withColumn(self, *a): return self
        def format(self, value): assert value == "delta"; return self
        def mode(self, value): assert value == "overwrite"; return self
        def partitionBy(self, *cols): assert cols == ("variant", "archive_month"); return self
        def saveAsTable(self, table): assert table == args().full_table
        def sql(self, query): return self
        def first(self): return {"version": 7}
        def table(self, table): return self
        def where(self, predicate): calls.append(("where", predicate)); return self
        def count(self): return actual
    monkeypatch.setitem(sys.modules, "pyspark", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pyspark.sql", SimpleNamespace(functions=SimpleNamespace(lit=lambda x: x)))
    a = {**archive(), "paths": ["a.parquet", "b.parquet"], "games": 2}
    if actual == 1:
        with pytest.raises(ValueError, match="Row count mismatch"):
            insert.ingest_month(a, args(), Spark())
    else:
        assert insert.ingest_month(a, args(), Spark())["version"] == 7
        assert ("replaceWhere", "variant = 'standard' AND archive_month = '2024-01'") in calls
        assert ("versionAsOf", 7) in calls
        assert ("paths", ("a.parquet", "b.parquet")) in calls


def test_period_validation():
    common.validate_period(0, 0)
    common.validate_period(2025, 9)
    for year, month in ((2025, 0), (0, 9), (2012, 1), (2025, 13)):
        with pytest.raises(ValueError): common.validate_period(year, month)


def test_bundle_and_entrypoint():
    import tomllib
    import yaml
    repo = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((repo / "resources/lichess_job.yml").read_text())
    job = config["resources"]["jobs"]["lichess_pipeline"]
    assert [t["task_key"] for t in job["tasks"]] == ["sync"]
    assert job["tasks"][0]["python_wheel_task"]["entry_point"] == "sync"
    assert tomllib.loads((repo / "pyproject.toml").read_text())["project"]["scripts"] == {"sync": "lichess_pipeline.cli:sync"}


def test_direct_reader_passes_schema_and_time_expression(monkeypatch):
    calls = []
    class Reader:
        @property
        def read(self): return self
        def schema(self, schema): calls.append(("schema", schema)); return self
        def parquet(self, *paths): calls.append(("paths", paths)); return self
        def withColumn(self, name, expr): calls.append((name, expr)); return self
    monkeypatch.setitem(sys.modules, "pyspark", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pyspark.sql", SimpleNamespace(functions=SimpleNamespace(expr=lambda x: x)))
    monkeypatch.setitem(sys.modules, "pyspark.sql.types", SimpleNamespace(StructType=SimpleNamespace(fromJson=lambda x: x)))
    spec = {"read_schema": {"type": "struct", "fields": []},
            "paths": ["raw-a.parquet", "raw-b.parquet"], "time_columns": ["UTCTime"]}
    insert.read_source(spec, Reader())
    assert ("schema", spec["read_schema"]) in calls
    assert ("paths", tuple(spec["paths"])) in calls
    expression = dict(calls)["UTCTime"]
    assert "IS NULL THEN CAST(NULL AS STRING)" in expression
    assert "%02d:%02d:%02d.%03d" in expression
    assert "pmod(`UTCTime`, 1000)" in expression


def test_completed_rewrite_checkpoint_is_still_reused(tmp_path, monkeypatch):
    root = tmp_path / "runs" / "new"
    a = {**archive(), "games": 2, "version": 9, "paths": ["old/prepared/file.parquet"]}
    common.record_completion(tmp_path / "runs" / "old", args(), a, "insert")
    monkeypatch.setattr(sync, "discover_months", lambda *a: [archive()])
    monkeypatch.setattr(sync, "stage_month", lambda *a: pytest.fail("Must not reload completed month"))
    sync.sync_dataset(args(), None, root)
    assert common.read_json(root / "delta.json") == []
