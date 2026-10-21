"""Check SQL before it reaches Trino: one read-only query over lake.curated / lake.gold only.

Trino would refuse anything else anyway (user chess-mcp is read-only on those two schemas, and
the catalog itself is read-only); this gives the model a clear error early and caps the rows.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

ALLOWED_SCHEMAS = ("curated", "gold")
CATALOG = "lake"
# Anything that writes, changes metadata or isn't a plain query.
FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
    exp.TruncateTable, exp.Grant, exp.Use, exp.Set, exp.Transaction, exp.Commit, exp.Rollback,
)


class SqlRejected(ValueError):
    """The SQL isn't a single read-only query over the allowed tables."""


@dataclass(frozen=True)
class CheckedSql:
    sql: str            # what runs: normalized, fully qualified, with a LIMIT
    tables: list[str]   # schema.table it reads


def check(sql: str, max_rows: int) -> CheckedSql:
    """Parse, validate and limit one query (pure; raises SqlRejected)."""
    if not sql or not sql.strip():
        raise SqlRejected("empty query")
    try:
        statements = [s for s in sqlglot.parse(sql, read="trino") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SqlRejected(f"not valid Trino SQL: {str(e).splitlines()[0]}") from e
    if len(statements) != 1:
        raise SqlRejected("send exactly one statement")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SqlRejected(f"only SELECT queries are allowed, not {tree.key.upper()}")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN):
            raise SqlRejected(f"only SELECT queries are allowed ({node.key.upper()} found)")

    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = []
    for t in tree.find_all(exp.Table):
        name, schema, catalog = t.name.lower(), t.db.lower(), t.catalog.lower()
        if not schema and name in ctes:
            continue  # a WITH name, not a table
        if isinstance(t.this, exp.Func) or not name:
            raise SqlRejected("table functions aren't allowed")
        if not schema:
            raise SqlRejected(f"qualify table {name!r} with its schema: curated.{name} or gold.{name}")
        if catalog and catalog != CATALOG:
            raise SqlRejected(f"only the {CATALOG!r} catalog is available, not {catalog!r}")
        if schema not in ALLOWED_SCHEMAS:
            raise SqlRejected(f"only schemas {', '.join(ALLOWED_SCHEMAS)} are available, not {schema!r}")
        t.set("catalog", exp.to_identifier(CATALOG))
        tables.append(f"{schema}.{name}")

    limit = tree.args.get("limit")
    current = None
    if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal) and limit.expression.is_int:
        current = int(limit.expression.this)
    if current is None or current > max_rows + 1:
        tree = tree.limit(max_rows + 1)  # one extra row tells us the result was truncated
    return CheckedSql(sql=tree.sql(dialect="trino"), tables=sorted(set(tables)))
