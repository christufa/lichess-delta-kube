"""A fake Trino: the API's real SQL runs against SQLite tables shaped like lake.gold."""
from __future__ import annotations

import re
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from chess_api.app import create_app
from chess_api.settings import Settings
from chess_api.trino_client import TrinoUnavailable

SCHEMA = """
CREATE TABLE monthly_summary (month TEXT, speed TEXT, games INT, players INT, white_wins INT, draws INT,
                              black_wins INT, unfinished INT, bot_games INT, avg_rating REAL);
CREATE TABLE openings (month TEXT, speed TEXT, eco TEXT, opening TEXT, rating_band INT, games INT,
                       white_wins INT, draws INT, black_wins INT, unfinished INT);
CREATE TABLE rating_distribution (month TEXT, speed TEXT, rating_bucket INT, player_games INT, players INT);
"""

SUMMARY = [
    # month, speed, games, players, white_wins, draws, black_wins, unfinished, bot_games, avg_rating
    ("2015-11", "all", 100, 40, 50, 5, 44, 1, 2, 1600.0),
    ("2015-11", "blitz", 60, 30, 30, 3, 27, 0, 1, 1620.5),
    ("2015-11", "bullet", 40, 20, 20, 2, 17, 1, 1, 1570.0),
    ("2015-12", "all", 120, 50, 60, 6, 52, 2, 0, 1610.0),
    ("2015-12", "blitz", 70, 35, 35, 3, 31, 1, 0, 1630.0),
    ("2015-12", "bullet", 49, 25, 25, 3, 21, 0, 0, 1580.0),
    ("2015-12", None, 1, 2, 0, 0, 0, 1, 0, None),  # unparseable time control
]
OPENINGS = [
    # month, speed, eco, opening, rating_band, games, white_wins, draws, black_wins, unfinished
    ("2015-11", "blitz", "C20", "King's Pawn Game", 1600, 30, 16, 1, 13, 0),
    ("2015-11", "blitz", "B01", "Scandinavian Defense", 1600, 20, 9, 1, 10, 0),
    ("2015-11", "bullet", "C20", "King's Pawn Game", 1400, 25, 12, 1, 12, 0),
    ("2015-12", "blitz", "B01", "Scandinavian Defense", 1600, 40, 20, 2, 18, 0),
    ("2015-12", "blitz", "C20", "King's Pawn Game", 1800, 5, 3, 0, 2, 0),
    ("2015-12", "bullet", "A00", "Polish Opening", None, 2, 1, 0, 0, 1),
]
RATINGS = [
    # month, speed, rating_bucket, player_games, players
    ("2015-12", "blitz", 1500, 60, 20),
    ("2015-12", "blitz", 1550, 80, 15),
    ("2015-12", "bullet", None, 4, 2),
    ("2015-12", "bullet", 1500, 94, 23),
    ("2015-11", "blitz", 1500, 120, 30),
]


class FakeTrino:
    """Runs queries on SQLite after dropping the `lake.gold.` prefix; refuses anything outside gold."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.executemany("INSERT INTO monthly_summary VALUES (?,?,?,?,?,?,?,?,?,?)", SUMMARY)
        self.conn.executemany("INSERT INTO openings VALUES (?,?,?,?,?,?,?,?,?,?)", OPENINGS)
        self.conn.executemany("INSERT INTO rating_distribution VALUES (?,?,?,?,?)", RATINGS)
        self.lock = threading.Lock()
        self.calls: list[tuple[str, list]] = []
        self.down = False

    def query(self, sql, params=()):
        with self.lock:
            self.calls.append((sql, list(params)))
            if self.down:
                raise TrinoUnavailable("connection refused")
            for ref in re.findall(r"\blake\.\w+\.\w+", sql):
                assert ref.startswith("lake.gold."), f"query outside gold: {ref}"
            cur = self.conn.execute(sql.replace("lake.gold.", ""), list(params))
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


@pytest.fixture
def fake() -> FakeTrino:
    return FakeTrino()


@pytest.fixture
def settings() -> Settings:
    return Settings(rate_limit_per_second=0)  # rate limiting has its own tests


@pytest.fixture
def client(fake, settings) -> TestClient:
    return TestClient(create_app(settings, fake))
