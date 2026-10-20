import datetime as dt

import pytest

from lichess_spark.cost import evaluate, month_bounds, parse_rows, query_body, recent_months
from lichess_spark.wap import gate


def test_month_bounds():
    assert month_bounds("2026-02") == ("2026-02-01T00:00:00Z", "2026-02-28T23:59:59Z")
    assert month_bounds("2024-02") == ("2024-02-01T00:00:00Z", "2024-02-29T23:59:59Z")
    assert month_bounds("2026-12") == ("2026-12-01T00:00:00Z", "2026-12-31T23:59:59Z")


def test_recent_months():
    assert recent_months(dt.date(2026, 10, 20)) == ["2026-09", "2026-10"]
    assert recent_months(dt.date(2027, 1, 1)) == ["2026-12", "2027-01"]


def test_query_body():
    b = query_body("2026-10")
    assert b["type"] == "ActualCost" and b["dataset"]["granularity"] == "Daily"
    assert [g["name"] for g in b["dataset"]["grouping"]] == ["ServiceName", "ResourceType"]


@pytest.mark.parametrize("cost_name", ["Cost", "PreTaxCost", "totalCost"])
def test_parse_rows(cost_name):
    response = {"properties": {
        "columns": [{"name": cost_name}, {"name": "UsageDate"}, {"name": "ServiceName"},
                    {"name": "ResourceType"}, {"name": "Currency"}],
        "rows": [[1.25, 20261019, "Virtual Machines", "microsoft.compute/virtualmachinescalesets", "USD"],
                 [-0.5, 20261020, "Storage", "microsoft.storage/storageaccounts", "USD"]],
    }}
    rows = parse_rows(response, "rg-x", "2026-10")
    assert rows[0] == ("2026-10", dt.date(2026, 10, 19), "rg-x", "Virtual Machines",
                       "microsoft.compute/virtualmachinescalesets", 1.25, "USD")
    assert rows[1][5] == -0.5


def test_parse_rows_without_cost_column():
    with pytest.raises(ValueError):
        parse_rows({"properties": {"columns": [{"name": "UsageDate"}], "rows": []}}, "rg", "2026-10")


def test_evaluate():
    ok = {"rows": 10, "out_of_month": 0, "currencies": 1, "negative": 0}
    assert gate(evaluate("2026-10", ok))
    assert not gate(evaluate("2026-10", {**ok, "out_of_month": 1}))
    assert not gate(evaluate("2026-10", {**ok, "currencies": 2}))
    assert gate(evaluate("2026-10", {**ok, "rows": 0, "currencies": 0}))   # an empty 1st of the month publishes
    assert gate(evaluate("2026-10", {**ok, "negative": 2}))                # credits are counted, not blocking
