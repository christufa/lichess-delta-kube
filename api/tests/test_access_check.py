from __future__ import annotations

from chess_api.access_check import run
from chess_api.trino_client import TrinoUnavailable


class RulesFake:
    """Answers like Trino with the chess-api rules: gold yes, everything else denied."""

    def __init__(self, user="chess-api", curated_readable=False):
        self.user, self.curated_readable = user, curated_readable

    def query(self, sql, params=()):
        if "current_user" in sql:
            return [{"who": self.user}]
        if "lake.gold." in sql or self.curated_readable:
            return [{"n": 7}]
        table = sql.split("FROM ")[1].split()[0]
        raise TrinoUnavailable(f'TrinoUserError(message="Access Denied: Cannot select from columns in {table}")')


def test_passes_with_the_intended_rules():
    lines = []
    assert run(RulesFake(), lines.append)
    assert lines[0] == "signed in as: chess-api" and lines[-1] == "OK"


def test_fails_if_curated_is_readable():
    assert not run(RulesFake(curated_readable=True), lambda _: None)


def test_fails_as_another_user():
    assert not run(RulesFake(user="lichess-jobs"), lambda _: None)


def test_other_errors_are_not_mistaken_for_denials():
    class Down(RulesFake):
        def query(self, sql, params=()):
            if "lake.curated.games" in sql:
                raise TrinoUnavailable("connection refused")
            return super().query(sql, params)

    assert not run(Down(), lambda _: None)
