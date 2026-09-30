"""Tool: pytest. What: Python's standard test runner. Why: guardrails must be proven, not assumed."""

import os

import pytest

from app.safety import UnsafeSQLError, validate_sql

SAFE = [
    "SELECT * FROM orders",
    "SELECT s.country, COUNT(*) FROM orders o JOIN stores s ON s.store_id = o.store_id GROUP BY 1",
    "WITH m AS (SELECT date_trunc('month', order_date) AS mth FROM orders) SELECT mth, COUNT(*) FROM m GROUP BY 1",
    "SELECT channel FROM orders UNION SELECT 'x'",
    "SELECT COUNT(*) FILTER (WHERE status = 'Cancelled') FROM orders LIMIT 5",
]

UNSAFE = [
    "DELETE FROM orders",
    "UPDATE products SET unit_price = 0",
    "INSERT INTO stores VALUES (1)",
    "DROP TABLE customers",
    "TRUNCATE orders",
    "ALTER TABLE orders ADD COLUMN x INT",
    "CREATE TABLE x (a INT)",
    "GRANT ALL ON orders TO public",
    "SELECT * FROM orders; DELETE FROM orders",
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM pg_user",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM kb.chunks",
    "SELECT * INTO backup FROM orders",
    "SELECT * FROM orders FOR UPDATE",
    "COPY orders TO '/tmp/x'",
    "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
    "SELECT set_config('statement_timeout', '0', false)",
    "",
]


@pytest.mark.parametrize("sql", SAFE)
def test_safe_queries_pass(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize("sql", UNSAFE)
def test_unsafe_queries_rejected(sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(sql)


def test_limit_added_when_missing():
    assert validate_sql("SELECT * FROM orders", max_rows=50).endswith("LIMIT 50")


def test_existing_limit_kept():
    assert validate_sql("SELECT * FROM orders LIMIT 5").endswith("LIMIT 5")


@pytest.mark.skipif(not os.getenv("RUN_DB_TESTS"), reason="needs a bootstrapped database")
def test_database_rejects_writes_even_if_validator_is_bypassed():
    import psycopg

    from app.db import run_readonly

    with pytest.raises(psycopg.Error):
        run_readonly("DELETE FROM orders")
