"""SQL guardrail: layer 1 of defence in depth.

Tool: sqlglot
  What: a SQL parser. It turns a SQL string into a syntax tree we can inspect.
  Why:  never trust LLM output. Instead of fragile regex ("does it contain DELETE?"),
        we parse the query and check its structure: exactly one read-only statement,
        only allowed tables, no dangerous functions, and a row LIMIT.
"""

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {"stores", "customers", "products", "orders", "order_items"}

FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "dblink", "dblink_exec", "lo_import", "lo_export", "pg_terminate_backend",
    "pg_cancel_backend", "set_config", "pg_reload_conf", "query_to_xml", "copy",
}

_WRITE_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "AlterTable",
    "TruncateTable", "Command", "Grant", "Revoke", "Copy", "Set",
)
WRITE_NODES = tuple(getattr(exp, n) for n in _WRITE_NODE_NAMES if hasattr(exp, n))
READ_ROOTS = tuple(getattr(exp, n) for n in ("Select", "Union", "Intersect", "Except", "SetOperation") if hasattr(exp, n))


class UnsafeSQLError(ValueError):
    """Raised when a query fails validation. The message is fed back to the SQL agent."""


def validate_sql(sql: str, max_rows: int = 200) -> str:
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        raise UnsafeSQLError("Empty query.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQLError(f"SQL syntax error: {str(e).splitlines()[0]}") from e

    if len(statements) != 1:
        raise UnsafeSQLError("Exactly one SQL statement is allowed.")
    stmt = statements[0]

    if not isinstance(stmt, READ_ROOTS):
        raise UnsafeSQLError("Only SELECT queries are allowed.")

    for node in stmt.walk():
        if isinstance(node, WRITE_NODES):
            raise UnsafeSQLError(f"Write or DDL operation is not allowed ({type(node).__name__}).")
        if isinstance(node, exp.Select) and (node.args.get("into") or node.args.get("locks")):
            raise UnsafeSQLError("SELECT INTO and row locks are not allowed.")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in FORBIDDEN_FUNCTIONS:
                raise UnsafeSQLError(f"Function '{name}' is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if table.db and table.db.lower() != "public":
            raise UnsafeSQLError(f"Schema '{table.db}' is not allowed.")
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise UnsafeSQLError(f"Table '{name}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_TABLES))}.")

    normalized = stmt.sql(dialect="postgres")
    limit = stmt.args.get("limit")
    if limit is None:
        return f"SELECT * FROM ({normalized}) AS q LIMIT {max_rows}"
    return normalized
