"""Small DB helpers shared by the Phase 3 human-loop code (nodes 11-13, escalation, reviewer API).

Connections are autocommit; every tenant-scoped statement group runs in `scoped()`, a transaction that sets
app.tenant_id LOCALLY, so a pooled connection never carries a tenant into the next request.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg

from . import db

DEFAULT_ACT_DSN = "postgresql://a7_act:a7_act_dev@localhost:55432/a7"


def act_dsn() -> str:
    return os.environ.get("A7_ACT_DATABASE_URL", DEFAULT_ACT_DSN)


def connect_app() -> psycopg.Connection:
    """app_user connection (RLS enforced), autocommit."""
    return db.connect(app=True, autocommit=True)


def connect_act() -> psycopg.Connection:
    """a7_act connection: the only login that can SELECT pii_vault. Used by act.render_outbound only."""
    return psycopg.connect(act_dsn(), autocommit=True)


@contextmanager
def scoped(conn: psycopg.Connection, tenant_id: str) -> Iterator[psycopg.Connection]:
    if not tenant_id:
        raise ValueError("tenant_id required")
    with conn.transaction():
        conn.execute("SELECT a7.set_tenant(%s, true)", (tenant_id,))
        yield conn


@contextmanager
def maybe_conn(conn: psycopg.Connection | None) -> Iterator[psycopg.Connection]:
    """Use the given connection, or open (and close) an app_user one."""
    if conn is not None:
        yield conn
        return
    c = connect_app()
    try:
        yield c
    finally:
        c.close()
