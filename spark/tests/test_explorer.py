import pytest

from lichess_spark import explorer
from lichess_spark.wap import gate


def test_position_key_drops_en_passant_and_counters():
    fen = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2"
    assert explorer.position_key(fen) == "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq"


def test_start_position():
    assert explorer.replay([]) == explorer.START


def test_move_orders_reaching_one_position_share_a_key():
    a = explorer.replay("Nf3 d5 d4".split())
    b = explorer.replay("d4 d5 Nf3".split())
    assert a == b and a.endswith(" b KQkq")


def test_castling_rights_and_side_to_move_are_in_the_key():
    castled = explorer.replay("e4 e5 Nf3 Nc6 Bc4 Bc5 O-O".split())
    assert castled.split()[1:] == ["b", "kq"]


def test_san_with_check_and_annotation_free_tokens():
    assert explorer.replay("e4 e5 Bc4 Nc6 Qh5 Nf6 Qxf7#".split()) is not None


def test_illegal_move_gives_none():
    assert explorer.replay("e4 e4".split()) is None
    assert explorer.replay(["Zz9"]) is None


@pytest.mark.parametrize("avg, band", [(None, None), (800, 0), (1199.5, 0), (1200, 1200), (1999, 1600),
                                       (2000, 2000), (2399, 2000), (2850, 2400)])
def test_rating_band(avg, band):
    assert explorer.rating_band(avg) == band


def stats(**over):
    return {"rows": 100, "joined_games": 1000, "start_games": 995, "results_over_games": 0,
            "kept_pairs": 500, "unparsed_pairs": 0, **over}


def by_name(checks):
    return {c.name: c for c in checks}


def test_evaluate_pass():
    checks = explorer.evaluate(stats(), 1000)
    assert gate(checks) and all(c.passed for c in checks)


@pytest.mark.parametrize("over, check", [
    ({"rows": 0}, "not_empty"),
    ({"joined_games": 998}, "games_joined_once"),
    ({"joined_games": 1002}, "games_joined_once"),
    ({"start_games": 1001}, "start_not_overcounted"),
    ({"results_over_games": 1}, "results_within_games"),
    ({"unparsed_pairs": 6}, "moves_replay"),
])
def test_evaluate_blocking(over, check):
    checks = explorer.evaluate(stats(**over), 1000)
    assert not by_name(checks)[check].passed and not gate(checks)


def test_low_start_coverage_only_warns():
    checks = explorer.evaluate(stats(start_games=900), 1000)
    assert not by_name(checks)["start_coverage"].passed and gate(checks)
