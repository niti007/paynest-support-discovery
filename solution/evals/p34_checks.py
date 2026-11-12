"""Phase 3+4 integration checks on the REAL integrated graph (Bedrock eu-north-1 profile paynest, compose Postgres, mock payroll API).

    uv run python -m solution.evals.p34_checks            (env: A7_*_DATABASE_URL, A7_VALKEY_URL for the compose project)

1. Cache end to end: a How-To ticket is drafted, a reviewer approves it, act stores it in answer_cache; a second identical How-To
   ticket (new id, same tenant) gets a cache_lookup HIT through the live graph, the agent is skipped, output_guard + human_approval
   still run, and with the kill switch OFF only a `shadow` outbox row exists.
   Test-only steps, stated plainly: the classifier is a labelled stub (How-To / normal / P3) so the lane does not depend on Nova's label,
   and for ticket 1 route.decide_lane is forced to draft_for_agent, because with the current rules a non-P1 How-To is always
   auto_answer (no human approval) and a P1 is never cached, so no approved How-To draft can arise on its own (reported as an open issue).
2. Meridian replay through the whole graph (real classify via Qwen3-32B, real dedupe on the cached Cohere embeddings, real route):
   the incident opens at PN-101239 and that ticket's lane is `incident`. The draft LLM is scripted (the lane, not the draft, is under test).
Writes solution/evals/reports/phase34_checks.json (ids, lanes, counts only).
"""
from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from langgraph.types import Command

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "solution/evals/reports/phase34_checks.json"
HOWTO_ID, DUP_ID = "PN-101333", "PN-101333-CACHE2"


def start_mock():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "solution.a7.mock_payroll.app:app", "--port", str(port)],
                         env={**os.environ, "A7_PAYROLL_API_TOKEN": "a7-mock-token-dev"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close(); break
        except OSError:
            time.sleep(0.5)
    os.environ["A7_PAYROLL_API_URL"] = f"http://127.0.0.1:{port}"
    return p


class HowToStub:
    """Test-only labels a human would give for a How-To ticket."""
    name = "howto-stub"

    def decide_multi(self, items):
        from solution.a7.decide.base import Decision
        d = lambda c: Decision(c, 0.95, "stub", 0.0, {})
        return {"category": d("howto"), "urgency": d("normal"), "is_injection": d("no"), "priority": d("P3")}


def cache_e2e(res: dict) -> None:
    from solution.a7 import db
    from solution.a7.decide.base import get_decider
    from solution.a7.graph import build_graph, postgres_checkpointer, thread_config
    from solution.a7.nodes import route as route_mod
    from solution.a7.nodes.normalise import make_normalise
    from solution.a7.nodes.pii_gate import make_pii_gate
    from solution.a7.vault import LocalKeyProvider, PostgresVaultStore, Vault
    from solution.a7.worker import find_record, run_ticket

    nova = get_decider("bedrock_nova")
    ids = [HOWTO_ID, DUP_ID]
    with db.connect(autocommit=True) as o, postgres_checkpointer() as cp:
        for t in ids:
            cp.delete_thread(t)
            for tbl in ("approvals", "outbox", "ticket_embeddings"):
                o.execute(f"DELETE FROM {tbl} WHERE ticket_id=%s", (t,))
        o.execute("DELETE FROM answer_cache WHERE source_ticket_id = ANY(%s)", (ids,))
        o.execute("UPDATE feature_flags SET enabled=false WHERE name='auto_send_enabled'")
        gate = make_pii_gate(Vault(PostgresVaultStore(o), LocalKeyProvider()))
        norm = make_normalise()
        graph = build_graph(cp, deciders={"bedrock_nova": HowToStub(), "bedrock_qwen32b": HowToStub()}, output_decider=nova)

        # ticket 1: forced into draft_for_agent (see module docstring), real agent, real guard, reviewer approves
        orig = route_mod.decide_lane
        route_mod.decide_lane = lambda s: ("draft_for_agent", "General-L1", list(s.get("route_reasons", [])) + ["TEST_ONLY:forced draft_for_agent"])
        try:
            for attempt in range(1, 4):                 # a model draft can fail the guard; a failed draft is not cacheable, so retry the ticket
                cp.delete_thread(HOWTO_ID)
                o.execute("DELETE FROM approvals WHERE ticket_id=%s", (HOWTO_ID,))
                v1 = run_ticket(find_record(HOWTO_ID), graph, gate, norm)
                if (v1.get("guard") or {}).get("passed"):
                    break
        finally:
            route_mod.decide_lane = orig
        graph.invoke(Command(resume={"status": "approved", "reviewer": "p34-reviewer"}), thread_config(HOWTO_ID), durability="sync")
        f1 = graph.get_state(thread_config(HOWTO_ID)).values
        cache_action = [a for a in f1.get("actions", []) if a.get("type") == "cache"]
        stored = o.execute("SELECT count(*), min(topic), min(region) FROM answer_cache WHERE source_ticket_id=%s", (HOWTO_ID,)).fetchone()

        # ticket 2: identical text, new id, normal routing
        rec = copy.deepcopy(find_record(HOWTO_ID))
        for k, val in list(rec.items()):
            if val == HOWTO_ID:
                rec[k] = DUP_ID
        v2 = run_ticket(rec, graph, gate, norm)
        rows = o.execute("SELECT kind, status, shadow FROM outbox WHERE ticket_id=%s", (DUP_ID,)).fetchall()
        steps2 = [x.get("node") for x in v2.get("trace", [])]
        res["cache_e2e"] = {
            "ticket1": {"lane": v1.get("lane"), "topic": v1.get("topic"), "region": v1.get("region"), "cache_step": v1.get("cache"),
                        "tools": [c["name"] for c in v1.get("tool_calls") or []], "guard_passed": (v1.get("guard") or {}).get("passed"), "guard_failures": (v1.get("guard") or {}).get("failures"), "attempts": attempt,
                        "approval": (f1.get("approval") or {}).get("status"), "act_cache_action": cache_action,
                        "answer_cache_rows": {"n": stored[0], "topic": stored[1], "region": stored[2]}},
            "ticket2": {"lane": v2.get("lane"), "cache": v2.get("cache"), "cache_hit": v2.get("cache_hit"), "cached_from": v2.get("cached_from"),
                        "agent_ran": "agent" in steps2, "output_guard_ran": "output_guard" in steps2, "steps": steps2,
                        "guard_passed": (v2.get("guard") or {}).get("passed"), "approval": (v2.get("approval") or {}).get("status"),
                        "actions": [(a.get("type"), a.get("status")) for a in v2.get("actions") or []], "outbox": [list(r) for r in rows],
                        "draft_equals_cached": bool(v1.get("draft")) and v2.get("draft") == (f1.get("approval") or {}).get("edited_draft") or v2.get("draft") == v1.get("draft")},
            "kill_switch_off_only_shadow": bool(rows) and all(r[1] == "shadow" and r[2] is True for r in rows if r[0] == "reply"),
        }
        for t in ids:                                   # leave nothing behind
            cp.delete_thread(t)
        o.execute("DELETE FROM pii_vault WHERE ticket_id = ANY(%s)", (ids,))
        o.execute("DELETE FROM answer_cache WHERE source_ticket_id = ANY(%s)", (ids,))


def meridian(res: dict) -> None:
    import numpy as np
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver

    from solution.a7 import db
    from solution.a7.decide.base import get_decider
    from solution.a7.graph import build_graph, thread_config
    from solution.a7.nodes.dedupe_cluster import make_dedupe_cluster
    from solution.tests.p3_fakes import FakeChat

    cache = ROOT / "solution/evals/cache"
    corpus, vecs = json.loads((cache / "dedupe_corpus.json").read_text()), np.load(cache / "dedupe_vecs.npy")
    by_text = {r["text"]: vecs[i].tolist() for i, r in enumerate(corpus)}
    emb = lambda texts, input_type="search_document": [by_text[t] for t in texts]
    mer = sorted([r for r in corpus if r["tenant"] == "C-1002" and r["received_at"].startswith("2026-09-25")], key=lambda r: r["received_at"])
    inc_ids = [r["id"] for r in mer if r["incident"]]
    qwen = get_decider("bedrock_qwen32b")
    llm = lambda tier: FakeChat([AIMessage(content="A specialist will follow up shortly.")])

    class YesGuard:
        def decide(self, q, options, *, context=None):
            from solution.tests.p3_fakes import dec
            return dec("yes")

    with db.connect(autocommit=True) as o:
        o.execute("DELETE FROM ticket_embeddings WHERE tenant_id='C-1002'")
        o.execute("DELETE FROM incidents WHERE tenant_id='C-1002'")
        g = build_graph(MemorySaver(), deciders={"bedrock_nova": qwen, "bedrock_qwen32b": qwen}, llm=llm, output_decider=YesGuard(),
                        dedupe_node=make_dedupe_cluster(emb))
        first_incident, lanes = None, {}
        for r in mer:
            st = {"ticket_id": r["id"], "vault_key": r["id"], "source": "email", "channel": "email", "pii_gate_status": "passed",
                  "record": {"ticket_id": r["id"], "customer_id": "C-1002"}, "masked_text": r["text"], "pii_placeholders": {},
                  "received_at": r["received_at"], "received_tz": "+00:00", "customer_ref": "C-1002", "customer_ref_how": "given",
                  "category_raw": "Payroll", "category": "payroll", "language": "en", "is_payroll_window": True, "trace": []}
            cfg = thread_config(r["id"])
            g.invoke(st, cfg)
            v = g.get_state(cfg).values
            lanes[r["id"]] = v.get("lane")
            if v.get("lane") == "incident" and first_incident is None:
                first_incident = r["id"]
            if first_incident and len(lanes) >= inc_ids.index(first_incident) + 3 and r["id"] in inc_ids:
                pass
            if first_incident and sum(1 for x in lanes if x in inc_ids) >= 8:
                break
        n_inc = o.execute("SELECT count(*), max(array_length(ticket_ids,1)) FROM incidents WHERE tenant_id='C-1002'").fetchone()
        res["meridian"] = {"tickets_replayed": len(lanes), "incident_set_5th": inc_ids[4], "first_incident_lane_ticket": first_incident,
                           "lane_of_5th": lanes.get(inc_ids[4]), "lanes_of_first_5": [lanes.get(i) for i in inc_ids[:5]],
                           "incident_rows": n_inc[0], "incident_ticket_count": n_inc[1], "pass": first_incident == inc_ids[4] == "PN-101239"}
        o.execute("DELETE FROM ticket_embeddings WHERE tenant_id='C-1002'")
        o.execute("DELETE FROM incidents WHERE tenant_id='C-1002'")


def main() -> None:
    from solution.db import bootstrap
    mock = start_mock()
    res: dict = {}
    try:
        bootstrap.bootstrap()
        for fn in (cache_e2e, meridian):
            try:
                fn(res)
            except Exception as e:  # noqa: BLE001
                res[fn.__name__ + "_error"] = f"{type(e).__name__}: {str(e)[:300]}"
                raise
    finally:
        mock.terminate()
        OUT.write_text(json.dumps(res, indent=2, default=str))
        print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
