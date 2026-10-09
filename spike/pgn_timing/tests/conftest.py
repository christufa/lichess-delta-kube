import random

import pytest
import zstandard


def make_game(i: int, rng: random.Random) -> str:
    white, black = f"player{rng.randint(1, 500)}", f"player{rng.randint(1, 500)}"
    tags = [
        ("Event", "Rated Blitz game"),
        ("Site", f"https://lichess.org/g{i:07d}"),
        ("Date", "2025.09.01"),
        ("Round", "-"),
        ("White", white),
        ("Black", black),
        ("Result", rng.choice(["1-0", "0-1", "1/2-1/2"])),
        ("UTCDate", "2025.09.01"),
        ("UTCTime", f"{i % 24:02d}:{i % 60:02d}:00"),
        ("WhiteElo", str(rng.randint(800, 2800))),
        ("BlackElo", str(rng.randint(800, 2800))),
        ("WhiteRatingDiff", "+5"),
        ("BlackRatingDiff", "-5"),
        ("ECO", "C20"),
        ("Opening", "King's Pawn Game"),
        ("TimeControl", "180+2"),
        ("Termination", "Normal"),
    ]
    if i % 50 == 0:
        tags.append(("WhiteTitle", "BOT"))
    header = "\n".join(f'[{k} "{v}"]' for k, v in tags)
    plies = " ".join(f"{n}. e4 {{ [%clk 0:03:00] }} {n}... e5 {{ [%clk 0:03:00] }}" for n in range(1, rng.randint(5, 40)))
    return f"{header}\n\n{plies} 1-0\n\n"


@pytest.fixture
def sample_pgn_zst(tmp_path):
    rng = random.Random(42)
    n = 3000
    text = "".join(make_game(i, rng) for i in range(n))
    path = tmp_path / "sample.pgn.zst"
    path.write_bytes(zstandard.ZstdCompressor(level=3).compress(text.encode()))
    return path, n
