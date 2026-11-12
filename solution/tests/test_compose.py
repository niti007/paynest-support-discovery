import json
import os
import subprocess
import time
from pathlib import Path

import pytest
import valkey

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]


def _compose(*args):
    """`docker compose` already honours COMPOSE_PROJECT_NAME / COMPOSE_FILE from the environment; pass them through explicitly
    so a non-default project (e.g. a7int) is checked, and the default project when they are unset."""
    cmd = ["docker", "compose"]
    if os.environ.get("COMPOSE_PROJECT_NAME"):
        cmd += ["-p", os.environ["COMPOSE_PROJECT_NAME"]]
    for f in filter(None, os.environ.get("COMPOSE_FILE", "").split(os.pathsep)):
        cmd += ["-f", f]
    return subprocess.run(cmd + list(args), cwd=ROOT, capture_output=True, text=True, check=True).stdout


def test_services_healthy():
    out = _compose("ps", "--format", "json")
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    health = {r["Service"]: r.get("Health") for r in rows}
    assert health.get("postgres") == "healthy" and health.get("valkey") == "healthy", health


def test_valkey_set_get_ttl():
    r = valkey.Valkey.from_url(os.environ.get("A7_VALKEY_URL", "redis://localhost:56379/0"), socket_timeout=3)
    r.set("a7:test", "v", ex=30)
    assert r.get("a7:test") == b"v" and 0 < r.ttl("a7:test") <= 30
    r.set("a7:short", "v", px=100)
    time.sleep(0.3)
    assert r.get("a7:short") is None
    r.delete("a7:test")


def test_langfuse_profile_not_default():
    out = _compose("config", "--services").split()
    assert sorted(out) == ["postgres", "valkey"]
    _compose("--profile", "langfuse", "config", "-q")
