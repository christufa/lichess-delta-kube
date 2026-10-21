import pytest

from chess_mcp.sql_guard import SqlRejected, check


def test_select_is_qualified_and_limited():
    c = check("select speed, games from gold.monthly_summary where month = '2026-09'", 1000)
    assert "lake.gold.monthly_summary" in c.sql
    assert c.sql.rstrip().endswith("LIMIT 1001")
    assert c.tables == ["gold.monthly_summary"]


def test_existing_small_limit_is_kept():
    assert check("SELECT * FROM curated.games LIMIT 5", 1000).sql.endswith("LIMIT 5")


def test_large_limit_is_capped():
    assert check("SELECT * FROM curated.games LIMIT 999999", 1000).sql.endswith("LIMIT 1001")


def test_cte_and_join():
    c = check("WITH t AS (SELECT * FROM gold.openings) SELECT * FROM t JOIN lake.gold.monthly_summary m "
              "ON t.month = m.month", 100)
    assert c.tables == ["gold.monthly_summary", "gold.openings"]


@pytest.mark.parametrize("sql", [
    "INSERT INTO gold.openings SELECT * FROM gold.openings",
    "DELETE FROM curated.games",
    "UPDATE gold.openings SET games = 0",
    "DROP TABLE gold.openings",
    "CREATE TABLE gold.x AS SELECT 1",
    "ALTER TABLE gold.openings ADD COLUMN x int",
    "MERGE INTO gold.openings t USING gold.openings s ON t.eco = s.eco WHEN MATCHED THEN DELETE",
    "CALL lake.system.rollback_to_snapshot('gold', 'openings', 1)",
    "GRANT SELECT ON gold.openings TO bob",
    "SET SESSION query_max_execution_time = '1h'",
])
def test_writes_and_admin_are_rejected(sql):
    with pytest.raises(SqlRejected):
        check(sql, 100)


@pytest.mark.parametrize("sql, why", [
    ("SELECT * FROM raw.games", "schemas"),
    ("SELECT * FROM ops.azure_cost", "schemas"),
    ("SELECT * FROM system.runtime.queries", "catalog"),
    ("SELECT * FROM hive.default.t", "catalog"),
    ("SELECT * FROM games", "qualify"),
    ("SELECT 1; SELECT 2", "one statement"),
    ("", "empty"),
    ("SELEKT nonsense", "SQL|SELECT"),
])
def test_other_tables_and_shapes_are_rejected(sql, why):
    with pytest.raises(SqlRejected, match=why):
        check(sql, 100)


def test_subquery_into_forbidden_schema_is_rejected():
    with pytest.raises(SqlRejected):
        check("SELECT * FROM gold.openings WHERE eco IN (SELECT eco FROM raw.games)", 100)
