"""Database access.

Tool: PostgreSQL + psycopg 3
  What: PostgreSQL is the relational database holding the business data AND the vector
        knowledge base (via pgvector). psycopg is the Python driver.
  Why:  one database for data and vectors = less infrastructure. Postgres also gives
        real security primitives we rely on: read-only roles, read-only transactions,
        and statement timeouts.

Agent-generated SQL is protected in depth (the validator in safety.py is layer 1):
  2. It runs as `analyst_ro`, a role that only has SELECT on the business tables.
  3. Every query runs inside a READ ONLY transaction that is always rolled back.
  4. A statement timeout kills runaway queries.
"""

from datetime import date, datetime
from decimal import Decimal

import psycopg

from app.config import get_settings


def admin_connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().database_url)


def to_jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def run_readonly(sql: str) -> tuple[list[str], list[list]]:
    """Execute a validated SELECT as the read-only role. Returns (columns, rows)."""
    s = get_settings()
    with psycopg.connect(s.readonly_database_url, autocommit=False) as conn:
        try:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET LOCAL statement_timeout = {int(s.statement_timeout_ms)}")
            cur = conn.execute(sql)
            columns = [d.name for d in cur.description] if cur.description else []
            rows = [[to_jsonable(v) for v in row] for row in cur.fetchmany(s.max_rows)]
            return columns, rows
        finally:
            conn.rollback()


def ping() -> bool:
    try:
        with admin_connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False
