"""DB bootstrap: schema.sql, role passwords, LangGraph checkpoint tables + grants. Idempotent.

    A7_DATABASE_URL=<owner dsn> uv run python -m solution.db.bootstrap

Passwords (never printed):
  app_user : A7_APP_USER_PASSWORD, else the Secrets Manager secret in A7_APP_USER_SECRET_ARN (JSON {"password": ...})
  a7_act   : A7_ACT_PASSWORD,      else the secret in A7_ACT_SECRET_ARN
  a7_admin : A7_ADMIN_PASSWORD,    else the secret in A7_ADMIN_SECRET_ARN   (the only role that writes feature_flags)
  Only when A7_ENV is unset or "local" and neither is given do the dev defaults (app_user_dev / a7_act_dev) apply.
  On AWS (A7_ENV=dev/prod/...) a missing credential is an error: the dev password is never set there.
"""
from __future__ import annotations

import json
import os

import psycopg
from psycopg import sql

from solution.a7 import db

DEV_DEFAULTS = {"app_user": "app_user_dev", "a7_act": "a7_act_dev", "a7_admin": "a7_admin_dev"}
SOURCES = {"app_user": ("A7_APP_USER_PASSWORD", "A7_APP_USER_SECRET_ARN"),
           "a7_act": ("A7_ACT_PASSWORD", "A7_ACT_SECRET_ARN"),
           "a7_admin": ("A7_ADMIN_PASSWORD", "A7_ADMIN_SECRET_ARN")}
CHECKPOINT_TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")


def _secret_password(arn: str) -> str:
    import boto3
    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "eu-north-1"))
    raw = sm.get_secret_value(SecretId=arn)["SecretString"]
    try:
        return json.loads(raw)["password"]
    except (ValueError, KeyError, TypeError):
        return raw


def resolve_password(role: str) -> str:
    env_var, arn_var = SOURCES[role]
    if os.environ.get(env_var):
        return os.environ[env_var]
    if os.environ.get(arn_var):
        return _secret_password(os.environ[arn_var])
    if os.environ.get("A7_ENV", "local") == "local":
        return DEV_DEFAULTS[role]
    raise RuntimeError(f"no password for {role}: set {env_var} or {arn_var} (A7_ENV={os.environ.get('A7_ENV')})")


def bootstrap(owner_dsn: str | None = None) -> None:
    owner_dsn = owner_dsn or db.dsn()
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        db.apply_schema(conn)
        for role in ("app_user", "a7_act", "a7_admin"):
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(resolve_password(role))))
    from langgraph.checkpoint.postgres import PostgresSaver     # needs CREATE on the DB: owner only
    with PostgresSaver.from_conn_string(owner_dsn) as saver:
        saver.setup()
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        for t in CHECKPOINT_TABLES:
            conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO app_user").format(sql.Identifier(t)))


if __name__ == "__main__":
    bootstrap()
    print("bootstrap complete")
