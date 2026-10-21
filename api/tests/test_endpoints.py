from __future__ import annotations

from fastapi.testclient import TestClient

from chess_api.app import create_app
from chess_api.settings import Settings


def test_months(client):
    r = client.get("/api/v1/months")
    assert r.status_code == 200
    assert r.json() == {"months": ["2015-11", "2015-12"]}


def test_summary_all_speeds(client):
    rows = client.get("/api/v1/summary", params={"from": "2015-11", "to": "2015-12"}).json()["rows"]
    assert [(r["month"], r["speed"]) for r in rows] == [
        ("2015-11", "all"), ("2015-11", "blitz"), ("2015-11", "bullet"),
        ("2015-12", "all"), ("2015-12", "blitz"), ("2015-12", "bullet"), ("2015-12", None),
    ]
    dec = rows[3]
    assert dec == {
        "month": "2015-12", "speed": "all", "games": 120, "players": 50, "white_wins": 60, "draws": 6,
        "black_wins": 52, "unfinished": 2, "bot_games": 0, "avg_rating": 1610.0,
    }


def test_summary_filters_speeds_and_range(client):
    r = client.get("/api/v1/summary", params=[("from", "2015-12"), ("to", "2015-12"), ("speed", "all")])
    body = r.json()
    assert (body["from_month"], body["to_month"]) == ("2015-12", "2015-12")
    assert [(x["month"], x["speed"], x["games"]) for x in body["rows"]] == [("2015-12", "all", 120)]


def test_summary_rejects_bad_input(client):
    assert client.get("/api/v1/summary", params={"from": "2015-13", "to": "2015-12"}).status_code == 422
    assert client.get("/api/v1/summary", params={"from": "2015-11"}).status_code == 422
    assert client.get("/api/v1/summary", params={"from": "2015-11", "to": "2015-12", "speed": "x"}).status_code == 422
    r = client.get("/api/v1/summary", params={"from": "2015-12", "to": "2015-11"})
    assert r.status_code == 400 and "from" in r.json()["detail"]


def test_injection_attempt_is_rejected_before_sql(client, fake):
    r = client.get("/api/v1/summary", params={"from": "2015-11' OR 1=1 --", "to": "2015-12"})
    assert r.status_code == 422
    assert fake.calls == []


def test_rating_distribution(client):
    body = client.get("/api/v1/rating-distribution", params={"month": "2015-12"}).json()
    assert body["month"] == "2015-12" and body["bucket_width"] == 50
    assert [(r["speed"], r["rating_bucket"]) for r in body["rows"]] == [
        ("blitz", 1500), ("blitz", 1550), ("bullet", None), ("bullet", 1500),
    ]
    blitz = client.get("/api/v1/rating-distribution", params={"month": "2015-12", "speed": "blitz"}).json()
    assert {r["speed"] for r in blitz["rows"]} == {"blitz"}
    assert client.get("/api/v1/rating-distribution", params={"month": "2015-12", "speed": "all"}).status_code == 422


def test_openings_aggregate_over_months(client):
    body = client.get("/api/v1/openings", params={"from": "2015-11", "to": "2015-12"}).json()
    assert body["total_games"] == 122
    # ties broken by ECO, so the order is stable
    assert [(o["eco"], o["games"]) for o in body["openings"]] == [("B01", 60), ("C20", 60), ("A00", 2)]
    b01 = next(o for o in body["openings"] if o["eco"] == "B01")
    assert b01 == {
        "eco": "B01", "opening": "Scandinavian Defense", "games": 60, "white_wins": 29, "draws": 3,
        "black_wins": 28, "unfinished": 0,
    }


def test_openings_by_speed_band_and_limit(client):
    body = client.get(
        "/api/v1/openings", params={"from": "2015-11", "to": "2015-12", "speed": "blitz", "rating_band": 1600}
    ).json()
    assert (body["speed"], body["rating_band"], body["total_games"]) == ("blitz", 1600, 90)
    assert [(o["eco"], o["games"]) for o in body["openings"]] == [("B01", 60), ("C20", 30)]
    top1 = client.get("/api/v1/openings", params={"from": "2015-11", "to": "2015-12", "limit": 1}).json()
    assert len(top1["openings"]) == 1 and top1["total_games"] == 122
    empty = client.get("/api/v1/openings", params={"from": "2010-01", "to": "2010-02"}).json()
    assert empty["openings"] == [] and empty["total_games"] == 0
    bad = {"from": "2015-11", "to": "2015-12"}
    assert client.get("/api/v1/openings", params={**bad, "rating_band": 1650}).status_code == 422
    assert client.get("/api/v1/openings", params={**bad, "limit": 101}).status_code == 422


def test_trino_down_is_503_and_not_cached(client, fake):
    fake.down = True
    r = client.get("/api/v1/months")
    assert r.status_code == 503 and r.headers["retry-after"] == "30"
    assert r.json() == {"detail": "data store unavailable"}  # no Trino internals leak
    fake.down = False
    assert client.get("/api/v1/months").status_code == 200


def test_probes(client, fake):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    assert client.get("/readyz").status_code == 200
    assert [c[0] for c in fake.calls] == ["SELECT 1"]  # re-checked at most every 30 s


def test_readyz_fails_when_trino_is_down(fake):
    fake.down = True
    client = TestClient(create_app(Settings(rate_limit_per_second=0), fake))
    assert client.get("/readyz").status_code == 503
    assert client.get("/healthz").status_code == 200


def test_queries_only_touch_gold(client, fake):
    client.get("/api/v1/months")
    client.get("/api/v1/summary", params={"from": "2015-11", "to": "2015-12"})
    client.get("/api/v1/rating-distribution", params={"month": "2015-12"})
    client.get("/api/v1/openings", params={"from": "2015-11", "to": "2015-12"})
    assert len(fake.calls) == 4
    assert all("lake.gold." in sql and "curated" not in sql for sql, _ in fake.calls)


def test_internal_costs(client, fake, monkeypatch):
    import datetime as dt

    import chess_api.app as app_module

    class Today(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 10, 21, tzinfo=tz)

    monkeypatch.setattr(app_module.dt, "datetime", Today)
    r = client.get("/internal/v1/costs", params={"months": 2})  # 2026-09 and 2026-10
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_month"] == "2026-09" and body["currency"] == "USD"
    assert body["rows"] == [
        {"date": "2026-10-19", "service": "Azure Kubernetes Service", "cost": 2.0},
        {"date": "2026-10-19", "service": "Storage", "cost": 1.0},  # summed across resource groups
        {"date": "2026-10-20", "service": "Azure Kubernetes Service", "cost": 3.0},
    ]
    sql, params = fake.calls[-1]
    assert "lake.ops.azure_cost" in sql and params == ["2026-09"]


def test_internal_costs_is_not_public(client, fake):
    # Through Cloudflare (it adds cf-connecting-ip): 404, and Trino isn't asked.
    assert client.get("/internal/v1/costs", headers={"cf-connecting-ip": "203.0.113.9"}).status_code == 404
    assert not fake.calls
    assert client.get("/internal/v1/costs", params={"months": 0}).status_code == 422
    assert "/internal/v1/costs" not in client.get("/api/openapi.json").json()["paths"]
