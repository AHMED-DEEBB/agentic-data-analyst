"""One-command, idempotent setup. Safe to run on every container start.

    python -m app.bootstrap            # tables + data + read-only role + knowledge base
    python -m app.bootstrap --skip-kb  # skip embeddings (used by fast CI tests)
"""

import argparse
import time

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.config import get_settings
from app.db import admin_connect
from app.seed import seed


def wait_for_db(retries: int = 30) -> None:
    for _ in range(retries):
        try:
            with admin_connect() as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            time.sleep(1)
    raise RuntimeError("Database not reachable.")


def create_readonly_role(conn) -> None:
    ro = conninfo_to_dict(get_settings().readonly_database_url)
    user, password = ro["user"], ro["password"]
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
    if not exists:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(user), sql.Literal(password)))
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
        sql.Identifier(conn.info.dbname), sql.Identifier(user)))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(user)))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(user)))
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-kb", action="store_true")
    args = parser.parse_args()

    wait_for_db()
    with admin_connect() as conn:
        inserted = seed(conn)
        print(f"[bootstrap] business data {'loaded' if inserted else 'already present'}")
        create_readonly_role(conn)
        print("[bootstrap] read-only role ready")
        if not args.skip_kb:
            from app.retrieval import ingest

            print(f"[bootstrap] knowledge base: {ingest(conn)} chunks embedded")


if __name__ == "__main__":
    main()
