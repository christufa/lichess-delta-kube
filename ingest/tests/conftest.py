import hashlib
import random
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
import zstandard


def make_game(i: int, rng: random.Random, month: str) -> str:
    tags = [
        ("Event", "Rated Blitz game"),
        ("Site", f"https://lichess.org/{month.replace('-', '')}{i:07d}"),
        ("Date", month.replace("-", ".") + ".01"),
        ("Round", "-"),
        ("White", f"player{rng.randint(1, 500)}"),
        ("Black", f"player{rng.randint(1, 500)}"),
        ("Result", rng.choice(["1-0", "0-1", "1/2-1/2"])),
        ("UTCDate", month.replace("-", ".") + ".01"),
        ("UTCTime", f"{i % 24:02d}:{i % 60:02d}:00"),
        ("WhiteElo", str(rng.randint(800, 2800))),
        ("BlackElo", str(rng.randint(800, 2800))),
        ("ECO", "C20"),
        ("Opening", "King's Pawn Game"),
        ("TimeControl", "180+2"),
        ("Termination", "Normal"),
    ]
    header = "\n".join(f'[{k} "{v}"]' for k, v in tags)
    moves = " ".join(f"{n}. e4 {{ [%clk 0:03:00] }} {n}... e5" for n in range(1, rng.randint(5, 40)))
    return f"{header}\n\n{moves} 1-0\n\n"


class Handler(SimpleHTTPRequestHandler):
    hits: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).hits.append(self.path)
        path = self.translate_path(self.path)
        try:
            data = open(path, "rb").read()
        except OSError:
            self.send_error(404)
            return
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(data):
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        self.wfile.write(data[start:])


class FakeLichess:
    """Serves list.txt / counts.txt / sha256sums.txt and monthly .pgn.zst files."""

    def __init__(self, root, base_url):
        self.root, self.base_url = root, base_url
        self.months: dict[str, dict] = {}

    def add_month(self, month: str, games: int, seed: int = 0, count_override: int | None = None,
                  sha_override: str | None = None):
        rng = random.Random(seed or hash(month) & 0xFFFF)
        text = "".join(make_game(i, rng, month) for i in range(games))
        name = f"lichess_db_standard_rated_{month}.pgn.zst"
        payload = zstandard.ZstdCompressor(level=3).compress(text.encode())
        (self.root / name).write_bytes(payload)
        self.months[month] = {"name": name, "games": count_override if count_override is not None else games,
                              "sha": sha_override or hashlib.sha256(payload).hexdigest()}
        self.publish()

    def publish(self):
        months = sorted(self.months, reverse=True)
        (self.root / "list.txt").write_text("".join(f"{self.base_url}/{self.months[m]['name']}\n" for m in months))
        # counts.txt is not sorted on the real site either
        (self.root / "counts.txt").write_text("".join(f"{self.months[m]['name']} {self.months[m]['games']}\n"
                                                      for m in sorted(self.months, key=lambda m: m[::-1])))
        (self.root / "sha256sums.txt").write_text("".join(f"{self.months[m]['sha']}  {self.months[m]['name']}\n"
                                                          for m in sorted(self.months)))


@pytest.fixture
def lichess(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    Handler.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake = FakeLichess(root, f"http://127.0.0.1:{server.server_address[1]}")
    yield fake
    server.shutdown()
