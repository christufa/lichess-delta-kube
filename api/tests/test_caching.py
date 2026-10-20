from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from chess_api.app import create_app
from chess_api.caching import RateLimiter, ResponseCache
from chess_api.settings import Settings


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_responses_are_cached_with_http_headers(client, fake):
    params = {"from": "2015-11", "to": "2015-12"}
    first = client.get("/api/v1/summary", params=params)
    second = client.get("/api/v1/summary", params=params)
    assert len(fake.calls) == 1
    assert (first.headers["x-cache"], second.headers["x-cache"]) == ("miss", "hit")
    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]
    assert first.headers["cache-control"].startswith("public, max-age=")
    assert first.headers["content-type"] == "application/json"


def test_speed_order_and_duplicates_share_a_cache_entry(client, fake):
    client.get("/api/v1/summary", params=[("from", "2015-11"), ("to", "2015-12"), ("speed", "blitz"), ("speed", "all")])
    client.get("/api/v1/summary", params=[("from", "2015-11"), ("to", "2015-12"), ("speed", "all"), ("speed", "blitz"),
                                          ("speed", "all")])
    assert len(fake.calls) == 1


def test_if_none_match_gives_304(client):
    etag = client.get("/api/v1/months").headers["etag"]
    r = client.get("/api/v1/months", headers={"If-None-Match": etag})
    assert r.status_code == 304 and r.content == b"" and r.headers["etag"] == etag
    assert client.get("/api/v1/months", headers={"If-None-Match": '"other"'}).status_code == 200


def test_cache_expires():
    clock = Clock()
    cache = ResponseCache(ttl_seconds=60, max_entries=10, timer=clock)
    calls = []
    compute = lambda: calls.append(1) or b"{}"  # noqa: E731
    assert cache.get_or_compute("k", compute)[1] is False
    assert cache.get_or_compute("k", compute)[1] is True
    clock.now += 61
    assert cache.get_or_compute("k", compute)[1] is False
    assert len(calls) == 2


def test_concurrent_misses_run_the_query_once():
    cache = ResponseCache(ttl_seconds=60, max_entries=10)
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.2)
        return b"{}"

    threads = [threading.Thread(target=cache.get_or_compute, args=("k", slow)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1


def test_failed_compute_is_not_cached():
    cache = ResponseCache(ttl_seconds=60, max_entries=10)

    def boom():
        raise RuntimeError("down")

    try:
        cache.get_or_compute("k", boom)
    except RuntimeError:
        pass
    assert cache.get_or_compute("k", lambda: b"{}")[1] is False


def test_token_bucket():
    clock = Clock()
    rl = RateLimiter(per_second=2, burst=3, timer=clock)
    assert [rl.take("a") for _ in range(3)] == [0, 0, 0]
    assert rl.take("a") == 0.5  # empty: next token in 1/2 s
    assert rl.take("b") == 0  # other IPs have their own bucket
    clock.now += 0.5
    assert rl.take("a") == 0
    clock.now += 100
    assert [rl.take("a") for _ in range(3)] == [0, 0, 0]  # refilled to the burst, not beyond
    assert rl.take("a") > 0


def test_rate_limit_per_client_ip(fake):
    client = TestClient(create_app(Settings(rate_limit_per_second=0.01, rate_limit_burst=2), fake))
    a = {"CF-Connecting-IP": "203.0.113.1"}
    assert [client.get("/api/v1/months", headers=a).status_code for _ in range(3)] == [200, 200, 429]
    r = client.get("/api/v1/months", headers=a)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert client.get("/api/v1/months", headers={"CF-Connecting-IP": "203.0.113.2"}).status_code == 200
    # probes are never limited
    assert all(client.get("/healthz", headers=a).status_code == 200 for _ in range(5))
