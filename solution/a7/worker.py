"""Local worker entry: pii_gate + normalise OUTSIDE the graph, then the graph on the masked record.

    uv run python -m solution.a7.worker PN-100402 PN-101055 ...      (AWS_PROFILE=paynest, compose up)

Raw text exists only inside pii_gate; the graph input has the masked record and vault refs (vault_key, placeholders) only.
The graph runs until it finishes or pauses at human_approval (interrupt); the checkpoint is keyed by thread_id = ticket id.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from . import db
from .graph import build_graph, thread_config
from .nodes.normalise import make_normalise
from .nodes.pii_gate import identify, make_pii_gate
from .state import TicketState

DATA = Path(__file__).resolve().parents[2] / "data-room" / "data"


def prepare(raw_record: dict[str, Any], gate: Callable, normalise_node: Callable) -> TicketState:
    """pii_gate then normalise. Returns the graph input (no raw_record). pii_gate_status may be 'blocked'."""
    state = gate({"raw_record": raw_record})
    assert "raw_record" not in state
    if state.get("pii_gate_status") != "passed":
        return state
    return normalise_node(state)


def run_ticket(raw_record: dict[str, Any], graph: Any, gate: Callable, normalise_node: Callable) -> dict[str, Any]:
    state = prepare(raw_record, gate, normalise_node)
    if state.get("pii_gate_status") != "passed":
        return {**state, "lane": "human_review", "route_reasons": ["pii_gate_blocked"]}
    cfg = thread_config(state["ticket_id"])
    result = graph.invoke(state, cfg, durability="sync")
    values = dict(graph.get_state(cfg).values)
    values["__interrupt__"] = bool(result.get("__interrupt__")) if isinstance(result, dict) else False
    return values


def find_record(ticket_id: str) -> dict[str, Any]:
    for t in json.loads((DATA / "tickets_export.json").read_text())["tickets"]:
        if ticket_id in (t.get("ticket_id"), t.get("tkt"), t.get("reference")):
            return t
    for t in json.loads((DATA / "whatsapp_export.json").read_text()):
        if ticket_id in (t.get("ticket_id"), t.get("thread_id")):
            return t
    for t in json.loads((DATA / "call_center_notes.json").read_text()):
        if ticket_id in (t.get("ticket_id"), t.get("call_id")):
            return t
    raise KeyError(ticket_id)


def summary(v: dict[str, Any]) -> dict[str, Any]:
    return {"ticket": v.get("ticket_id"), "tenant": v.get("tenant_id"), "tenant_how": v.get("tenant_how"), "lane": v.get("lane"),
            "category": v.get("category"), "suggested": v.get("suggested_category"), "conf": v.get("category_confidence"),
            "urgency": v.get("urgency"), "priority": v.get("priority"), "tier": v.get("llm_tier"),
            "citations": v.get("citations"), "tool_calls": v.get("tool_calls"), "guard": v.get("guard"),
            "reasons": v.get("route_reasons"), "paused": v.get("__interrupt__")}


def main(ids: list[str]) -> list[dict[str, Any]]:
    from .graph import postgres_checkpointer, setup_checkpointer
    from .vault import LocalKeyProvider, PostgresVaultStore, Vault

    setup_checkpointer()
    out = []
    # NOTE: the vault store upserts (ON CONFLICT DO UPDATE) but app_user has no UPDATE on pii_vault; the owner connection is
    # used locally until Builder C's role split (a7 gate role) lands.
    with db.connect(autocommit=True) as vconn, postgres_checkpointer() as cp:
        gate = make_pii_gate(Vault(PostgresVaultStore(vconn), LocalKeyProvider()))
        graph = build_graph(cp)
        norm = make_normalise()
        for tid in ids:
            cp.delete_thread(tid)
            v = run_ticket(find_record(tid), graph, gate, norm)
            out.append(v)
            print(json.dumps(summary(v), ensure_ascii=False, default=str), flush=True)
    return out


if __name__ == "__main__":
    main(sys.argv[1:])
