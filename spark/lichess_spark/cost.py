"""ops.azure_cost: daily Azure costs of this project's resource groups, a month at a time (#31).

    source  Azure Cost Management query API (ActualCost, daily, by service and resource type),
            one query per resource group in COST_RESOURCE_GROUPS, signed in as the jobs
            identity (workload identity; Cost Management Reader on those groups only)
    table   lake.ops.azure_cost   partitioned by month

In `ops`, not `gold`: gold is readable by the public API and the MCP server; costs are for the
owner (Trino user lichess-jobs, notebooks).

build_month(): write-audit-publish (lichess_spark.wap). A month is rebuilt whole each time;
costs for a day keep settling for ~72 h, so the daily run rebuilds the current and the
previous month.
  blocking   usage dates inside the month, one currency
  counted    rows present (the 1st of a month may have none yet), negative costs (credits)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import wap
from .wap import Check

TABLE = "lake.ops.azure_cost"
API = "https://management.azure.com"
API_VERSION = "2023-11-01"
COST_COLUMNS = ("totalCost", "Cost", "PreTaxCost")  # the cost column's name depends on the billing account type
GROUPING = ("ServiceName", "ResourceType")
SCHEMA = ("month string, usage_date date, resource_group string, service_name string, resource_type string, "
          "cost double, currency string")


def month_bounds(month: str) -> tuple[str, str]:
    """'2026-10' → ('2026-10-01T00:00:00Z', '2026-10-31T23:59:59Z')."""
    first = dt.date.fromisoformat(month + "-01")
    nxt = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return f"{first}T00:00:00Z", f"{nxt - dt.timedelta(days=1)}T23:59:59Z"


def recent_months(today: dt.date | None = None) -> list[str]:
    """The previous and the current month: the ones whose costs can still change."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    prev = today.replace(day=1) - dt.timedelta(days=1)
    return [prev.strftime("%Y-%m"), today.strftime("%Y-%m")]


def query_body(month: str, cost_name: str = "Cost") -> dict:
    start, end = month_bounds(month)
    return {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": start, "to": end},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": cost_name, "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": g} for g in GROUPING],
        },
    }


def parse_rows(response: dict, resource_group: str, month: str) -> list[tuple]:
    """One Cost Management query response page → rows for SCHEMA (pure, unit-tested)."""
    props = response.get("properties") or {}
    names = [c["name"] for c in props.get("columns", [])]
    idx = {n.lower(): i for i, n in enumerate(names)}
    cost_i = next((idx[c.lower()] for c in COST_COLUMNS if c.lower() in idx), None)
    if cost_i is None:
        raise ValueError(f"no cost column in {names}")
    date_i, cur_i = idx["usagedate"], idx.get("currency")
    svc_i, typ_i = idx.get("servicename"), idx.get("resourcetype")
    out = []
    for r in props.get("rows", []):
        d = str(int(r[date_i]))  # 20261019
        out.append((month, dt.date(int(d[:4]), int(d[4:6]), int(d[6:])), resource_group,
                    r[svc_i] if svc_i is not None else None, r[typ_i] if typ_i is not None else None,
                    float(r[cost_i]), r[cur_i] if cur_i is not None else None))
    return out


def evaluate(month: str, stats: dict) -> list[Check]:
    n = lambda key: int(stats.get(key) or 0)  # noqa: E731
    return [
        Check("usage_date_in_month", n("out_of_month") == 0, n("out_of_month"), 0, f"usage_date in {month}"),
        Check("single_currency", n("currencies") <= 1, n("currencies"), 1),
        Check("not_empty", n("rows") > 0, n("rows"), None, "the 1st of a month may have no costs yet",
              blocking=False),
        Check("no_negative_costs", n("negative") == 0, n("negative"), 0, "credits and refunds", blocking=False),
    ]


# ---------------------------------------------------------------------------- Azure side

def _http(method: str, url: str, body: bytes | None = None, headers: dict | None = None, tries: int = 6) -> dict:
    for attempt in range(tries):
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < tries - 1:  # Cost Management throttles per scope
                wait = e.headers.get("Retry-After") or e.headers.get("x-ms-ratelimit-microsoft.costmanagement-entity-retry-after")
                time.sleep(float(wait or 2 ** attempt * 5))
                continue
            raise RuntimeError(f"{method} {url}: {e.code} {e.read()[:500]!r}") from e
    raise RuntimeError("unreachable")


def arm_token(env=None) -> str:
    """An ARM access token for the pod's workload identity (no SDK: one token exchange)."""
    env = os.environ if env is None else env
    authority = env.get("AZURE_AUTHORITY_HOST", "https://login.microsoftonline.com/").rstrip("/")
    with open(env["AZURE_FEDERATED_TOKEN_FILE"]) as fh:
        assertion = fh.read().strip()
    form = urllib.parse.urlencode({
        "client_id": env["AZURE_CLIENT_ID"],
        "scope": API + "/.default",
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
    }).encode()
    resp = _http("POST", f"{authority}/{env['AZURE_TENANT_ID']}/oauth2/v2.0/token", form,
                 {"Content-Type": "application/x-www-form-urlencoded"})
    return resp["access_token"]


def fetch(resource_group: str, month: str, token: str, env=None) -> list[tuple]:
    """All rows for one resource group and month, following nextLink pages."""
    env = os.environ if env is None else env
    url = (f"{API}/subscriptions/{env['AZURE_SUBSCRIPTION_ID']}/resourceGroups/{resource_group}"
           f"/providers/Microsoft.CostManagement/query?api-version={API_VERSION}")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    rows = []
    for cost_name in ("Cost", "PreTaxCost"):
        try:
            page = _http("POST", url, json.dumps(query_body(month, cost_name)).encode(), headers)
            break
        except RuntimeError as e:
            if cost_name == "PreTaxCost" or " 400 " not in str(e):
                raise
    while True:
        rows += parse_rows(page, resource_group, month)
        nxt = (page.get("properties") or {}).get("nextLink")
        if not nxt:
            return rows
        page = _http("POST", nxt, json.dumps(query_body(month, cost_name)).encode(), headers)


def fetch_month(month: str, env=None) -> list[tuple]:
    env = os.environ if env is None else env
    token = arm_token(env)
    groups = [g.strip() for g in env["COST_RESOURCE_GROUPS"].split(",") if g.strip()]
    return [row for g in groups for row in fetch(g, month, token, env)]


# ---------------------------------------------------------------------------- Spark side

def ensure_table(spark, sample_df) -> None:
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.ops")
    if not wap.table_exists(spark, TABLE):
        ddl = sample_df._jdf.schema().toDDL()
        spark.sql(f"""
            CREATE TABLE {TABLE} ({ddl})
            USING iceberg
            PARTITIONED BY (month)
            TBLPROPERTIES ('format-version' = '2', 'write.parquet.compression-codec' = 'zstd',
                           'comment' = 'Daily Azure costs of the project resource groups by service and resource type (Cost Management, ActualCost). Owner only.')""")
    wap.ensure_main(spark, TABLE, sample_df.schema)
    wap.ensure_dq_table(spark)


def branch_stats(spark, branch: str, month: str) -> dict:
    return spark.sql(f"""
        SELECT count(*) AS rows,
               count_if(date_format(usage_date, 'yyyy-MM') <> '{month}') AS out_of_month,
               count(DISTINCT currency) AS currencies,
               count_if(cost < 0) AS negative
        FROM {TABLE} VERSION AS OF '{branch}'
        WHERE month = '{month}'""").first().asDict()


def build_month(spark, month: str, run_id: str | None = None, rows: list[tuple] | None = None) -> list[Check]:
    """Fetch (unless rows are given) and write-audit-publish one month."""
    from pyspark.sql import functions as F

    run_id = run_id or uuid.uuid4().hex
    rows = fetch_month(month) if rows is None else rows
    df = (spark.createDataFrame(rows, SCHEMA)
          .withColumn("_run_id", F.lit(run_id))
          .withColumn("_fetched_at", F.current_timestamp()))
    ensure_table(spark, df)
    audit = lambda branch: evaluate(month, branch_stats(spark, branch, month))  # noqa: E731
    return wap.publish(spark, TABLE, month, df, audit, run_id)
