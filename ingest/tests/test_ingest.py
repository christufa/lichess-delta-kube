import json
import os
import subprocess
import sys
import uuid

import pyarrow.parquet as pq
import pytest

from lichess_ingest import cli
from lichess_ingest.dest import LocalDest, open_dest
from lichess_ingest.parse import OUTPUT_SCHEMA
from lichess_ingest.source import fetch_inventory, select_months

from conftest import Handler

FAST = ["--workers", "2", "--chunk-mb", "0.5", "--zstd-level", "3", "--log-every", "1"]


def run(lichess, dest, work, *extra, months=None):
    argv = ["--dest", str(dest), "--base-url", lichess.base_url, "--log-every", "1"]
    if months:
        argv += ["--months", months]
    return cli.main(argv + ["run", "--work", str(work), "--workers", "2", "--chunk-mb", "0.5",
                            "--zstd-level", "3", *extra])


def manifest(dest_dir, month):
    return json.loads((dest_dir / "_state" / "raw_games" / f"{month}.json").read_text())


def month_files(dest_dir, month):
    d = dest_dir / "raw" / "games" / f"month={month}"
    return sorted(d.glob("*.parquet")) if d.exists() else []


# --------------------------------------------------------------------------- inventory

def test_inventory_and_month_selection(lichess):
    for m, n in [("2013-01", 50), ("2013-02", 60), ("2014-01", 70)]:
        lichess.add_month(m, n)
    entries = fetch_inventory(lichess.base_url)
    assert [e.month for e in entries] == ["2013-01", "2013-02", "2014-01"]  # oldest first
    assert entries[1].games == 60 and len(entries[1].sha256) == 64
    assert [e.month for e in select_months(entries, "2013")] == ["2013-01", "2013-02"]
    assert [e.month for e in select_months(entries, "2013-02:2014-01")] == ["2013-02", "2014-01"]
    assert [e.month for e in select_months(entries, "2014-01,2013-01")] == ["2013-01", "2014-01"]
    with pytest.raises(ValueError):
        select_months(entries, "2020-01")


# --------------------------------------------------------------------------- end to end, local destination

def test_ingests_months_with_manifests_and_lineage(lichess, tmp_path):
    lichess.add_month("2013-01", 3000)
    lichess.add_month("2013-02", 2000)
    dest, work = tmp_path / "lake", tmp_path / "work"
    assert run(lichess, dest, work, "--file-mb", "0.01") == 0

    for month, games in [("2013-01", 3000), ("2013-02", 2000)]:
        m = manifest(dest, month)
        files = month_files(dest, month)
        assert m["games"] == games and m["sha256"] == lichess.months[month]["sha"]
        assert [f["name"].rsplit("/", 1)[1] for f in m["files"]] == [f.name for f in files]
        assert len(files) > 2  # rolled into several files at --file-mb 0.01
        assert sum(pq.ParquetFile(f).metadata.num_rows for f in files) == games
        assert m["stats"]["parity_check"] is True and m["stats"]["fallback_chunks"] == 0
        table = pq.read_table(files[0])
        assert table.schema.equals(OUTPUT_SCHEMA)
        assert set(table.column("month").to_pylist()) == {month}
        assert set(table.column("_source_file").to_pylist()) == {lichess.months[month]["name"]}

    assert not list((work / "src").glob("*.pgn.zst"))       # sources deleted after success
    records = [json.loads(l) for l in (work / "runs.jsonl").read_text().splitlines()]
    assert [r["status"] for r in records] == ["ok", "ok"]


def test_rerun_skips_done_months_without_downloading(lichess, tmp_path, capsys):
    lichess.add_month("2013-01", 500)
    dest, work = tmp_path / "lake", tmp_path / "work"
    run(lichess, dest, work)
    Handler.hits.clear()
    assert run(lichess, dest, work) == 0
    assert not [h for h in Handler.hits if h.endswith(".pgn.zst")]
    cli.main(["--dest", str(dest), "--base-url", lichess.base_url, "plan"])
    assert "2013-01  done" in capsys.readouterr().out


def test_max_months_and_month_filter(lichess, tmp_path):
    for m in ["2013-01", "2013-02", "2013-03"]:
        lichess.add_month(m, 200)
    dest, work = tmp_path / "lake", tmp_path / "work"
    run(lichess, dest, work, "--max-months", "1", months="2013-02:2013-03")
    assert month_files(dest, "2013-02") and not month_files(dest, "2013-03") and not month_files(dest, "2013-01")


def test_count_mismatch_fails_without_manifest(lichess, tmp_path):
    lichess.add_month("2013-01", 300, count_override=301)
    dest, work = tmp_path / "lake", tmp_path / "work"
    with pytest.raises(SystemExit) as exc:
        run(lichess, dest, work)
    assert exc.value.code == 1
    assert not (dest / "_state" / "raw_games" / "2013-01.json").exists()
    record = json.loads((work / "runs.jsonl").read_text())
    assert record["status"] == "failed" and "Lichess publishes 301" in record["error"]


def test_checksum_mismatch_moves_file_aside(lichess, tmp_path):
    lichess.add_month("2013-01", 300, sha_override="0" * 64)
    dest, work = tmp_path / "lake", tmp_path / "work"
    with pytest.raises(SystemExit):
        run(lichess, dest, work)
    assert list((work / "src").glob("*.bad"))
    assert not (dest / "_state").exists()


def test_keep_going_continues_after_a_failed_month(lichess, tmp_path):
    lichess.add_month("2013-01", 200, count_override=999)
    lichess.add_month("2013-02", 200)
    dest, work = tmp_path / "lake", tmp_path / "work"
    assert run(lichess, dest, work, "--keep-going") == 1
    assert month_files(dest, "2013-02") and (dest / "_state/raw_games/2013-02.json").exists()


def test_upstream_change_reingests_and_removes_old_files(lichess, tmp_path):
    lichess.add_month("2013-01", 400, seed=1)
    dest, work = tmp_path / "lake", tmp_path / "work"
    run(lichess, dest, work)
    old = {f.name for f in month_files(dest, "2013-01")}
    lichess.add_month("2013-01", 450, seed=2)  # Lichess republishes the month
    assert run(lichess, dest, work) == 0
    new = {f.name for f in month_files(dest, "2013-01")}
    assert new and not (old & new)
    assert manifest(dest, "2013-01")["games"] == 450


def test_orphans_from_a_crashed_attempt_are_cleaned(lichess, tmp_path):
    lichess.add_month("2013-01", 300)
    dest, work = tmp_path / "lake", tmp_path / "work"
    orphan = dest / "raw" / "games" / "month=2013-01" / "part-crashed-00-0000.parquet"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"partial upload")
    run(lichess, dest, work)
    assert not orphan.exists()
    assert manifest(dest, "2013-01")["stats"]["stale_files_removed"] == 1


def test_resumes_a_partial_download(lichess, tmp_path):
    lichess.add_month("2013-01", 800)
    work = tmp_path / "work"
    payload = (lichess.root / lichess.months["2013-01"]["name"]).read_bytes()
    (work / "src").mkdir(parents=True)
    (work / "src" / lichess.months["2013-01"]["name"]).write_bytes(payload[: len(payload) // 2])
    assert run(lichess, tmp_path / "lake", work) == 0
    record = json.loads((work / "runs.jsonl").read_text())
    assert record["download"]["resumed_from_bytes"] == len(payload) // 2


def test_console_entry_point(lichess, tmp_path):
    lichess.add_month("2013-01", 200)
    env = {**os.environ, "LICHESS_DEST": str(tmp_path / "lake"), "LICHESS_BASE_URL": lichess.base_url}
    proc = subprocess.run([sys.executable, "-m", "lichess_ingest", "run", "--work", str(tmp_path / "w"),
                           "--workers", "1"], capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "2013-01: committed" in proc.stderr


# --------------------------------------------------------------------------- destinations

def test_open_dest_parses_urls(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "sv=x&sig=y")
    d = open_dest("abfss://lake@myacct.dfs.core.windows.net/bootstrap")
    assert d.url == "https://myacct.blob.core.windows.net/lake/bootstrap" and d.auth == "sas"
    d = open_dest("https://myacct.blob.core.windows.net/lake")
    assert d.url == "https://myacct.blob.core.windows.net/lake" and d.prefix == ""
    assert isinstance(open_dest("/tmp/x"), LocalDest)
    with pytest.raises(ValueError):
        open_dest("s3://bucket/x")


@pytest.mark.azurite
def test_end_to_end_against_azurite(lichess, tmp_path, monkeypatch):
    blob_url = os.environ.get("AZURITE_BLOB_URL")  # e.g. http://127.0.0.1:10000/devstoreaccount1
    if not blob_url:
        pytest.skip("AZURITE_BLOB_URL not set")
    from azure.storage.blob import BlobServiceClient
    conn = ("DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
            "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
            f"BlobEndpoint={blob_url};")
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", conn)
    container = "lake" + uuid.uuid4().hex[:8]
    BlobServiceClient.from_connection_string(conn).create_container(container)

    lichess.add_month("2013-01", 1500)
    lichess.add_month("2013-02", 700)
    dest = f"{blob_url}/{container}/bootstrap"
    assert run(lichess, dest, tmp_path / "work", "--file-mb", "0.01") == 0

    d = open_dest(dest)
    m = d.get_json("_state/raw_games/2013-01.json")
    assert m["games"] == 1500
    assert d.list("raw/games/month=2013-01/") == [f["name"] for f in m["files"]]
    # skip on re-run, and the orphan clean-up works against blob storage too
    d.put_json("raw/games/month=2013-02/part-crashed.parquet", {"x": 1})
    assert run(lichess, dest, tmp_path / "work", "--force", months="2013-02") == 0
    assert "raw/games/month=2013-02/part-crashed.parquet" not in d.list("raw/games/month=2013-02/")


@pytest.mark.parametrize("target", [1, 100, 5000, 1 << 20])
def test_chunks_follow_target_and_never_split_a_game(target):
    import io
    from lichess_ingest.parse import chunk_to_table, iter_chunks
    game = b'[Event "x"]\n[Site "https://lichess.org/a"]\n\n1. e4 e5 1-0\n\n'
    data = game * 2000
    chunks = list(iter_chunks(io.BytesIO(data), target, read_size=1 << 16))
    assert b"".join(chunks) == data
    assert all(c.startswith(b"[") for c in chunks)
    assert sum(chunk_to_table(c)[0].num_rows for c in chunks) == 2000
    assert all(len(c) < target + 2 * len(game) for c in chunks)
