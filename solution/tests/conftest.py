import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "solution"))
DATA = ROOT / "data-room" / "data"


def pytest_configure(config):
    config.addinivalue_line("markers", "db: needs a throwaway Postgres container (docker, host port 55433)")


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
    from a7.vault import LocalKeyProvider, MemoryVaultStore, Vault
    return Vault(MemoryVaultStore(), LocalKeyProvider(master_key=bytes(range(32))))


@pytest.fixture()
def gate(vault):
    from a7.nodes.pii_gate import make_pii_gate
    return make_pii_gate(vault)
