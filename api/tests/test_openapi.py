"""Contract tests: the committed spec matches the code, and real responses match the spec."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from chess_api import openapi

COMMITTED = Path(__file__).resolve().parents[1] / "openapi.json"
SPEC = json.loads(COMMITTED.read_text(encoding="utf-8"))

DASHBOARD_CALLS = [
    ("/api/v1/months", {}),
    ("/api/v1/summary", {"from": "2015-11", "to": "2015-12"}),
    ("/api/v1/rating-distribution", {"month": "2015-12"}),
    ("/api/v1/openings", {"from": "2015-11", "to": "2015-12", "speed": "blitz"}),
    ("/api/v1/explorer", {}),
    ("/api/v1/explorer", {"from": "2015-11", "to": "2015-12", "speed": "blitz", "rating_band": 1600}),
]


def test_committed_spec_is_up_to_date():
    assert openapi.render() == COMMITTED.read_text(encoding="utf-8"), (
        "api/openapi.json is out of date: run `uv run python -m chess_api.openapi` and commit it"
    )


def test_operation_ids_are_stable():
    ids = {op["operationId"] for path in SPEC["paths"].values() for op in path.values()}
    assert {"get_months", "get_summary", "get_rating_distribution", "get_openings"} <= ids


def test_every_dashboard_path_is_tested():
    assert {p for p in SPEC["paths"] if p.startswith("/api/")} == {p for p, _ in DASHBOARD_CALLS}


def _schema(path: str, status: str) -> dict:
    ref = SPEC["paths"][path]["get"]["responses"][status]["content"]["application/json"]["schema"]
    # Resolve $refs against the whole spec (OpenAPI 3.1 schemas are JSON Schema 2020-12).
    return {"$ref": ref["$ref"], "components": SPEC["components"]}


@pytest.mark.parametrize(("path", "params"), DASHBOARD_CALLS)
def test_responses_match_the_spec(client, path, params):
    r = client.get(path, params=params)
    assert r.status_code == 200
    schema = _schema(path, "200")
    jsonschema.validate(r.json(), schema, cls=jsonschema.Draft202012Validator)


def test_error_responses_match_the_spec(client, fake):
    r = client.get("/api/v1/summary", params={"from": "2015-12", "to": "2015-11"})
    jsonschema.validate(r.json(), _schema("/api/v1/summary", "400"), cls=jsonschema.Draft202012Validator)
    fake.down = True
    r = client.get("/api/v1/months")
    jsonschema.validate(r.json(), _schema("/api/v1/months", "503"), cls=jsonschema.Draft202012Validator)


def test_check_mode_detects_drift(tmp_path, capsys):
    stale = tmp_path / "openapi.json"
    stale.write_text("{}\n", encoding="utf-8")
    assert openapi.main([str(stale), "--check"]) == 1
    assert openapi.main([str(stale)]) == 0
    assert openapi.main([str(stale), "--check"]) == 0
