import pytest

from lichess_spark.curated import evaluate, parse_time_control, speed_class
from lichess_spark.wap import Check, gate, ref


@pytest.mark.parametrize("tc, expected", [
    ("0+1", (0, 1, "bullet")),             # 40 s
    ("15+0", (15, 0, "ultrabullet")),
    ("30+0", (30, 0, "bullet")),           # bound is exclusive
    ("60+0", (60, 0, "bullet")),
    ("120+1", (120, 1, "bullet")),         # 160 s
    ("180+0", (180, 0, "blitz")),
    ("180+2", (180, 2, "blitz")),          # 260 s
    ("300+5", (300, 5, "rapid")),          # 500 s
    ("600+0", (600, 0, "rapid")),
    ("900+10", (900, 10, "rapid")),        # 1300 s
    ("1500+0", (1500, 0, "classical")),
    ("1800+30", (1800, 30, "classical")),
    ("-", (None, None, "correspondence")),
    ("", (None, None, None)),
    (None, (None, None, None)),
    ("abc", (None, None, None)),
    ("180", (None, None, None)),
])
def test_parse_time_control(tc, expected):
    assert parse_time_control(tc) == expected


def test_speed_bounds():
    assert [speed_class(b, 0) for b in (29, 30, 179, 180, 479, 480, 1499, 1500)] == [
        "ultrabullet", "bullet", "bullet", "blitz", "blitz", "rapid", "rapid", "classical"]


def good(rows=100):
    return {"rows": rows, "missing_game_id": 0, "bad_time_control": 0, "missing_played_at": 0, "wrong_month": 0,
            "duplicates": 0, "rating_out_of_range": 0, "undecided": 0, "unknown_termination": 0, "outside_month": 0}


def by_name(checks):
    return {c.name: c for c in checks}


def test_all_pass():
    checks = evaluate("2013-01", good(), 100)
    assert all(c.passed for c in checks) and gate(checks)


def test_lost_rows_block():
    checks = evaluate("2013-01", good(99), 100)
    assert not by_name(checks)["row_count_matches_raw"].passed and not gate(checks)


@pytest.mark.parametrize("key, check", [
    ("missing_game_id", "game_id_present"),
    ("bad_time_control", "time_control_parseable"),
    ("missing_played_at", "played_at_present"),
    ("wrong_month", "month_column_consistent"),
])
def test_structural_problems_block(key, check):
    checks = evaluate("2013-01", {**good(), key: 2}, 100)
    assert not by_name(checks)[check].passed and by_name(checks)[check].observed == 2
    assert not gate(checks)


@pytest.mark.parametrize("key, check", [
    ("duplicates", "game_id_unique"),
    ("rating_out_of_range", "rating_in_range"),
    ("undecided", "result_decided"),
    ("unknown_termination", "termination_known"),
    ("outside_month", "played_at_in_month"),
])
def test_unusual_values_are_counted_not_blocking(key, check):
    checks = evaluate("2013-01", {**good(), key: 7}, 100)
    c = by_name(checks)[check]
    assert not c.passed and not c.blocking and c.observed == 7
    assert gate(checks)


def test_empty_month_blocks():
    assert not gate(evaluate("2013-01", good(0), 0))


def test_gate_ignores_non_blocking():
    assert gate([Check("a", True, 1, 1), Check("b", False, 3, 0, blocking=False)])
    assert not gate([Check("a", False, 0, 1), Check("b", True, 0, 0, blocking=False)])


def test_ref():
    assert ref("lake.curated.games") == "curated.games"
