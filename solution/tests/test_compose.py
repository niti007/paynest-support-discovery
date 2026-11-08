import json
import subprocess
import time
from pathlib import Path

import pytest
import valkey

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]


def test_services_healthy():
    out = subprocess.run(["docker", "compose", "ps", "--format", "json"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    health = {r["Service"]: r.get("Health") for r in rows}
    assert health.get("postgres") == "healthy" and health.get("valkey") == "healthy", health


def test_valkey_set_get_ttl():
    r = valkey.Valkey.from_url("redis://localhost:56379/0", socket_timeout=3)
    r.set("a7:test", "v", ex=30)
    assert r.get("a7:test") == b"v" and 0 < r.ttl("a7:test") <= 30
    r.set("a7:short", "v", px=100)
    time.sleep(0.3)
    assert r.get("a7:short") is None
    r.delete("a7:test")


def test_langfuse_profile_not_default():
    out = subprocess.run(["docker", "compose", "config", "--services"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert sorted(out) == ["postgres", "valkey"]
    subprocess.run(["docker", "compose", "--profile", "langfuse", "config", "-q"], cwd=ROOT, check=True)
