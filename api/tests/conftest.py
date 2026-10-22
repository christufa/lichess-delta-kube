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
CREATE TABLE position_moves (month TEXT, position TEXT, move TEXT, speed TEXT, rating_band INT, games INT,
                             white_wins INT, draws INT, black_wins INT, rated_games INT, rating_sum REAL,
                             example_game_id TEXT, example_rating REAL);
CREATE TABLE azure_cost (month TEXT, usage_date TEXT, resource_group TEXT, service_name TEXT, resource_type TEXT,
                         cost REAL, currency TEXT);
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


COSTS = [
    # month, usage_date, resource_group, service_name, resource_type, cost, currency
    ("2026-08", "2026-08-31", "rg-a", "Storage", "storageaccounts", 0.5, "USD"),
    ("2026-10", "2026-10-19", "rg-a", "Storage", "storageaccounts", 0.25, "USD"),
    ("2026-10", "2026-10-19", "rg-b", "Storage", "storageaccounts", 0.75, "USD"),
    ("2026-10", "2026-10-19", "rg-b", "Azure Kubernetes Service", "managedclusters", 2.0, "USD"),
    ("2026-10", "2026-10-20", "rg-b", "Azure Kubernetes Service", "managedclusters", 3.0, "USD"),
]
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq"
AFTER_NF3_D5_D4 = "rnbqkbnr/ppp1pppp/8/3p4/3P4/5N2/PPP1PPPP/RNBQKB1R b KQkq"  # = 1. d4 d5 2. Nf3
POSITION_MOVES = [
    # month, position, move, speed, rating_band, games, white, draws, black, rated, rating_sum, example, ex_rating
    ("2015-11", START, "e4", "blitz", 1600, 10, 5, 1, 4, 10, 17000.0, "aaaaaaaa", 1800.0),
    ("2015-11", START, "e4", "bullet", 1200, 6, 3, 0, 3, 6, 8400.0, "bbbbbbbb", 1500.0),
    ("2015-11", START, "d4", "blitz", 2000, 5, 3, 1, 1, 5, 10500.0, "cccccccc", 2300.0),
    ("2015-12", START, "e4", "blitz", 1600, 8, 4, 1, 3, 7, 11900.0, "dddddddd", 1900.0),
    ("2015-12", START, "d4", "blitz", 2400, 9, 4, 2, 3, 9, 22500.0, "eeeeeeee", 2600.0),
    ("2015-12", START, "Nf3", "rapid", None, 5, 2, 2, 0, 0, 0.0, None, None),
    ("2015-12", AFTER_NF3_D5_D4, "Nf6", "blitz", 1600, 12, 6, 6, 0, 11, 18700.0, "ffffffff", 1750.0),
]
ALLOWED_OUTSIDE_GOLD = {"lake.ops.azure_cost"}  # the internal cost endpoint's one table


class MaxBy:
    """Trino's max_by(value, key) for SQLite: the value of the row with the largest key."""

    def __init__(self):
        self.best = None

    def step(self, value, key):
        if key is not None and (self.best is None or key > self.best[0]):
            self.best = (key, value)

    def finalize(self):
        return self.best[1] if self.best else None


class FakeTrino:
    """Runs queries on SQLite after dropping the `lake.<schema>.` prefix; refuses anything outside gold
    except lake.ops.azure_cost (what Trino's rules allow chess-api)."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.executemany("INSERT INTO monthly_summary VALUES (?,?,?,?,?,?,?,?,?,?)", SUMMARY)
        self.conn.executemany("INSERT INTO openings VALUES (?,?,?,?,?,?,?,?,?,?)", OPENINGS)
        self.conn.executemany("INSERT INTO rating_distribution VALUES (?,?,?,?,?)", RATINGS)
        self.conn.executemany("INSERT INTO azure_cost VALUES (?,?,?,?,?,?,?)", COSTS)
        self.conn.executemany("INSERT INTO position_moves VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", POSITION_MOVES)
        self.conn.create_aggregate("max_by", 2, MaxBy)  # Trino's max_by(value, key)
        self.lock = threading.Lock()
        self.calls: list[tuple[str, list]] = []
        self.down = False

    def query(self, sql, params=()):
        with self.lock:
            self.calls.append((sql, list(params)))
            if self.down:
                raise TrinoUnavailable("connection refused")
            for ref in re.findall(r"\blake\.\w+\.\w+", sql):
                assert ref.startswith("lake.gold.") or ref in ALLOWED_OUTSIDE_GOLD, f"query outside gold: {ref}"
            cur = self.conn.execute(sql.replace("lake.gold.", "").replace("lake.ops.", ""), list(params))
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
