import datetime as dt

import pytest

from lichess_spark import catalog_recovery as cr


@pytest.mark.parametrize("name, version", [
    ("00549-01a15b1f-c60c-7e70-8930-eff4dfb22caa.gz.metadata.json", 549),  # Lakekeeper
    ("00003-6b2b0b3e-1d7a-4b8e-9b6e-2f9c1c1f0a11.metadata.json", 3),
    ("v12.metadata.json", 12),                                            # Hadoop tables
    ("v2.gz.metadata.json", 2),
    ("00001-abc.metadata.json.gz", 1),
    ("snap-123-1-abc.avro", None),                                        # not metadata
    ("version-hint.text", None),
    ("x.metadata.json", None),
])
def test_metadata_version(name, version):
    assert cr.metadata_version(name) == version


def test_newest_metadata_is_the_highest_version_not_the_last_listed():
    names = ["00010-b.gz.metadata.json", "00009-a.gz.metadata.json", "snap-1.avro", "00100-c.gz.metadata.json"]
    assert cr.newest_metadata(names) == "00100-c.gz.metadata.json"
    assert cr.newest_metadata(["snap-1.avro"]) is None
    assert cr.newest_metadata([]) is None


def test_inventory_round_trip():
    entries = [cr.Entry("gold.openings", "abfss://lake@a/x/2", "abfss://lake@a/x/2/metadata/00002-b.metadata.json"),
               cr.Entry("curated.games", "abfss://lake@a/x/1", "abfss://lake@a/x/1/metadata/00001-a.metadata.json",
                        comment="Typed games")]
    text = cr.to_json(entries, now=dt.datetime(2026, 10, 21, tzinfo=dt.timezone.utc))
    assert '"written_at": "2026-10-21T00:00:00+00:00"' in text
    assert [e.table for e in cr.from_json(text)] == ["curated.games", "gold.openings"]  # sorted
    assert cr.from_json(text)[0].comment == "Typed games"


def test_inventory_path():
    assert cr.inventory_path("abfss://lake@a.dfs.core.windows.net/bootstrap/") == \
        "abfss://lake@a.dfs.core.windows.net/bootstrap/_catalog/inventory.json"
