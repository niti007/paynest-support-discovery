import json
from pathlib import Path

import pytest

from solution.a7 import db


@pytest.fixture(scope="session")
def owner():
    """Superuser connection (bypasses RLS). Skips if the compose stack is not up."""
    try:
        conn = db.connect(autocommit=True, connect_timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"postgres not reachable: {e}")
    yield conn
    conn.close()


@pytest.fixture()
def app():
    """app_user connection (RLS enforced)."""
    conn = db.connect(app=True, autocommit=True)
    yield conn
    conn.close()

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data-room" / "data"


@pytest.fixture(scope="session")
def tickets():
    return json.loads((DATA / "tickets_export.json").read_text())["tickets"]


@pytest.fixture(scope="session")
def by_id(tickets):
    out = {}
    for t in tickets:
        out.setdefault(t.get("ticket_id") or t.get("tkt") or t.get("reference"), t)
    return out


@pytest.fixture(scope="session")
def whatsapp():
    return json.loads((DATA / "whatsapp_export.json").read_text())


@pytest.fixture(scope="session")
def calls():
    return json.loads((DATA / "call_center_notes.json").read_text())


@pytest.fixture()
def vault():
    from solution.a7.vault import LocalKeyProvider, MemoryVaultStore, Vault
    return Vault(MemoryVaultStore(), LocalKeyProvider(master_key=bytes(range(32))))


@pytest.fixture()
def gate(vault):
    from solution.a7.nodes.pii_gate import make_pii_gate
    return make_pii_gate(vault)


@pytest.fixture(autouse=True)
def _stale_llm_off_unless_aws(request, monkeypatch):
    """The guard's stale-claim LLM check (nodes/stale_llm.py) calls Bedrock. Offline tests switch it off; tests that want it inject a
    fake `stale_checker` / `make_llm`, and `aws`-marked tests run it for real."""
    if "aws" not in request.keywords:
        monkeypatch.setenv("A7_STALE_LLM", "0")
