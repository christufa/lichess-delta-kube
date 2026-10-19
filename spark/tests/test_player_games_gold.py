import pytest

from lichess_spark import gold, player_games
from lichess_spark.wap import gate


def by_name(checks):
    return {c.name: c for c in checks}


def players(games=10, **over):
    return {"rows": 2 * games, "white_rows": games, "black_rows": games, "missing_player": 0, "wrong_month": 0,
            "undecided": 0, **over}


def test_player_games_pass():
    assert gate(player_games.evaluate("2013-01", players(), 10))


@pytest.mark.parametrize("over, check", [
    ({"rows": 19}, "two_rows_per_game"),
    ({"white_rows": 11, "black_rows": 9}, "one_white_row_per_game"),
    ({"missing_player": 1}, "player_present"),
    ({"wrong_month": 1}, "month_column_consistent"),
])
def test_player_games_blocking(over, check):
    checks = player_games.evaluate("2013-01", players(**over), 10)
    assert not by_name(checks)[check].passed and not gate(checks)


def test_player_games_undecided_is_counted():
    checks = player_games.evaluate("2013-01", players(undecided=2), 10)
    assert by_name(checks)["outcome_decided"].observed == 2 and gate(checks)


def test_gold_summary():
    ok = {"rows": 5, "total_games": 40, "speed_games": 40}
    assert gate(gold.evaluate(gold.SUMMARY, ok, 40))
    assert not gate(gold.evaluate(gold.SUMMARY, {**ok, "speed_games": 39}, 40))
    assert not gate(gold.evaluate(gold.SUMMARY, {**ok, "total_games": 41}, 40))


def test_gold_openings_and_ratings():
    assert gate(gold.evaluate(gold.OPENINGS, {"rows": 3, "games": 40}, 40))
    assert not gate(gold.evaluate(gold.OPENINGS, {"rows": 3, "games": 39}, 40))
    assert gate(gold.evaluate(gold.RATINGS, {"rows": 3, "player_games": 80}, 40))
    assert not gate(gold.evaluate(gold.RATINGS, {"rows": 3, "player_games": 40}, 40))


def test_gold_empty_blocks():
    assert not gate(gold.evaluate(gold.OPENINGS, {"rows": 0, "games": 0}, 0))


def test_gold_unknown_table():
    with pytest.raises(ValueError):
        gold.evaluate("lake.gold.nope", {}, 0)
