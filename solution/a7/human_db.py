"""Small DB helpers shared by the Phase 3 human-loop code (nodes 11-13, escalation, reviewer API).

Connections are autocommit; every tenant-scoped statement group runs in `scoped()`, a transaction that sets
app.tenant_id LOCALLY, so a pooled connection never carries a tenant into the next request.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg

from . import db

ACT_USER, ACT_DEV_PASSWORD = "a7_act", "a7_act_dev"
ADMIN_USER, ADMIN_DEV_PASSWORD = "a7_admin", "a7_admin_dev"


def role_dsn(user: str, password: str, base: str | None = None) -> str:
    """The app DSN (host, port, database, options) with a different login. Same derivation as the app DSN itself, so a
    non-default compose project (A7_APP_DATABASE_URL on another port) needs no second setting."""
    u = urlsplit(base or db.app_dsn())
    host = u.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}" + (f":{u.port}" if u.port else "")
    return urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment))


def act_dsn() -> str:
    """A7_ACT_DATABASE_URL, else derived from the app DSN (password: A7_ACT_PASSWORD, local dev default otherwise)."""
    return os.environ.get("A7_ACT_DATABASE_URL") or role_dsn(ACT_USER, os.environ.get("A7_ACT_PASSWORD") or ACT_DEV_PASSWORD)


def admin_dsn() -> str:
    """A7_ADMIN_DATABASE_URL, else derived from the app DSN. Only /admin/flags connects with it."""
    return os.environ.get("A7_ADMIN_DATABASE_URL") or role_dsn(ADMIN_USER, os.environ.get("A7_ADMIN_PASSWORD") or ADMIN_DEV_PASSWORD)


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
