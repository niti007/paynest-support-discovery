"""Postgres connection helper with tenant scoping (RLS).

A7_DATABASE_URL       owner/loader connection (default: compose postgres superuser; bypasses RLS)
A7_APP_DATABASE_URL   app_user connection (RLS enforced); used by tests and the graph
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg

DEFAULT_DSN = "postgresql://postgres:postgres@localhost:55432/a7"
DEFAULT_APP_DSN = "postgresql://app_user:app_user_dev@localhost:55432/a7"
SCHEMA_SQL = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def dsn() -> str:
    return os.environ.get("A7_DATABASE_URL", DEFAULT_DSN)


def app_dsn() -> str:
    return os.environ.get("A7_APP_DATABASE_URL", DEFAULT_APP_DSN)


def connect(app: bool = False, **kw) -> psycopg.Connection:
    """app=True connects as app_user (RLS enforced)."""
    return psycopg.connect(app_dsn() if app else dsn(), **kw)


@contextmanager
def tenant(conn: psycopg.Connection, tenant_id: str):
    """Scope `conn` to one tenant: sets app.tenant_id for the session, resets it on exit."""
    conn.execute("SELECT a7.set_tenant(%s, false)", (tenant_id,))
    try:
        yield conn
    finally:
        if not conn.closed:
            if conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
                conn.rollback()
            conn.execute("SELECT a7.set_tenant('', false)")


def apply_schema(conn: psycopg.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect(autocommit=True)
    try:
        conn.execute(SCHEMA_SQL.read_text())
        if not conn.autocommit:
            conn.commit()
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["apply"]:
        apply_schema()
        print("schema applied")
    else:
        print("usage: python -m solution.a7.db apply")
