import datetime as dt

import pytest

from lichess_spark.maintenance import DEFAULTS, PREFIX, cutoff, settings


def test_defaults_pick_sort_for_sorted_tables():
    assert settings({"sort-order": "game_id ASC NULLS FIRST"}).rewrite == "sort"
    assert settings({}).rewrite == "binpack"
    assert settings({}) == DEFAULTS.__class__("binpack", 5, 7, 3, 3)


def test_table_properties_override():
    s = settings({PREFIX + "rewrite": "none", PREFIX + "expire-days": "30", PREFIX + "retain-last": "10",
                  PREFIX + "orphan-days": "5", PREFIX + "min-input-files": "2", "sort-order": "x"})
    assert (s.rewrite, s.expire_days, s.retain_last, s.orphan_days, s.min_input_files) == ("none", 30, 10, 5, 2)


def test_bad_strategy_rejected():
    with pytest.raises(ValueError):
        settings({PREFIX + "rewrite": "zorder"})


def test_cutoff():
    now = dt.datetime(2026, 10, 19, 3, 0, tzinfo=dt.timezone.utc)
    assert cutoff(7, now) == "2026-10-12 03:00:00"
    assert cutoff(0, now) == "2026-10-19 03:00:00"
