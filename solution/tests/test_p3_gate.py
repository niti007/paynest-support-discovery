"""Gate A3: the real worker path on tickets A (PN-100402), B (PN-101055), C (PN-101333) and PN-101129.

Real classifier = Nova Lite (Qwen3-32B fallback) since the 2026-11-12 routing decision; Bedrock (Nova Lite / Qwen3-235B), Postgres (compose) and Builder B's tools + mock payroll API.
Needs: compose up, loaders run, AWS_PROFILE=paynest. Run with:  pytest -m aws solution/tests/test_p3_gate.py -s

test_gate_real_classifier   observational for the classifier-dependent expectations: the Decider's confidence decides whether
                            A and C reach their lane. B and the block ticket are strict.
test_gate_oracle_classifier same real LLM/DB/tools/guard, with a test-only classifier that answers the labels a human would
                            (statutory/urgent, payroll/high, howto/normal): proves boxes 6-10 meet the expectations.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import pytest

from solution.tests.p3_fakes import FakeDecider, deciders

pytestmark = [pytest.mark.aws, pytest.mark.db]
TICKETS = ["PN-101129", "PN-100402", "PN-101055", "PN-101333"]
RESULTS: dict[str, dict] = {}


@pytest.fixture(scope="module")
def mock_api():
    pytest.importorskip("solution.a7.tools")
    pytest.importorskip("solution.a7.mock_payroll.app")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, "A7_PAYROLL_API_TOKEN": "a7-mock-token-dev"}
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "solution.a7.mock_payroll.app:app", "--port", str(port)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close(); break
        except OSError:
            time.sleep(0.5)
    os.environ["A7_PAYROLL_API_URL"] = f"http://127.0.0.1:{port}"
    yield port
    p.terminate()


def _run(deciders_=None):
    from solution.a7 import db
    from solution.a7.graph import build_graph, postgres_checkpointer
    from solution.db import bootstrap
    from solution.a7.nodes.normalise import make_normalise
    from solution.a7.nodes.pii_gate import make_pii_gate
    from solution.a7.vault import LocalKeyProvider, MemoryVaultStore, Vault
    from solution.a7.worker import find_record, run_ticket

    bootstrap.bootstrap()
    gate = make_pii_gate(Vault(MemoryVaultStore(), LocalKeyProvider(master_key=bytes(range(32)))))
    out = {}
    with postgres_checkpointer(db.app_dsn()) as cp:
        graph = build_graph(cp, deciders=deciders_)
        norm = make_normalise()
        for t in TICKETS:
            cp.delete_thread(t)
            out[t] = run_ticket(find_record(t), graph, gate, norm)
    return out


def _table(tag, res):
    print(f"\n== {tag}")
    for t, v in res.items():
        print(t, v.get("lane"), v.get("priority"), v.get("llm_tier"), [c["source_id"] for c in v.get("citations") or []],
              [c["name"] for c in v.get("tool_calls") or []], (v.get("guard") or {}).get("passed"), v.get("category"),
              v.get("category_confidence"), v.get("suggested_category"))


def _common(res):
    blocked = res["PN-101129"]
    assert blocked["lane"] == "block" and not blocked.get("tool_calls") and not blocked.get("draft")
    b = res["PN-101055"]
    assert b["tenant_id"] == "C-1004" and b["lane"] == "route_queue" and b["queue"] == "Payroll-Ops"
    assert any(c["name"] in ("get_payroll_run_status", "list_payroll_runs") and c["ok"] for c in b["tool_calls"])  # either read-only status tool: the model picks and b["guard"]["passed"]
    c = res["PN-101333"]
    assert c["tenant_id"] == "C-1004"
    for t in ("PN-100402", "PN-101333"):
        d = res[t].get("draft") or ""
        assert "<IBAN" not in d and "<EID" not in d and "AE00" not in d and "784-" not in d


def test_gate_real_classifier(mock_api):
    res = _run()
    RESULTS["real"] = res
    _table("real classifier", res)
    _common(res)
    # A and C: the lane depends on the model's labels (observational; see the table and phase34_integration.md).
    # Invariants that must hold whatever Nova says: a statutory-looking ticket is never auto-answered unseen by a person
    # unless it was classified as something other than statutory, and no draft carries PII (checked in _common).
    for t in ("PN-100402", "PN-101333"):
        assert res[t]["lane"] in ("draft_for_agent", "auto_answer", "human_review", "route_queue"), (t, res[t]["lane"])
    # Statutory -> draft_for_agent is strict. (An earlier human_review outcome here was output_guard failing twice on a correct
    # draft that mentioned the old "14 columns" as superseded; the guard now tells "asserted as current" from "superseded".)
    if res["PN-100402"]["category"] == "statutory":
        assert res["PN-100402"]["lane"] == "draft_for_agent", (res["PN-100402"]["lane"], (res["PN-100402"].get("guard") or {}).get("failures"))


def test_gate_oracle_classifier(mock_api):
    class Oracle(FakeDecider):
        def decide_multi(self, items):
            from solution.tests.p3_fakes import dec
            txt = items[0].context
            if "WPS" in txt or "SIF" in txt:
                cat, urg = "statutory", "urgent"
            elif "payroll" in txt.lower() and "Processing" in txt:
                cat, urg = "payroll", "high"
            elif "IGNORE ALL PREVIOUS" in txt:
                return {"category": dec("billing"), "urgency": dec("normal"), "is_injection": dec("yes")}
            else:
                cat, urg = "howto", "normal"
            return {"category": dec(cat), "urgency": dec(urg), "is_injection": dec("no")}
    ds = deciders(Oracle(), Oracle())
    res = _run(ds)
    # the guard's yes/no decide() must stay the real Nova backend, not the oracle
    RESULTS["oracle"] = res
    _table("oracle classifier", res)
    _common(res)
    # What the oracle run guarantees is everything decided BEFORE the drafting LLM writes a word: the routed lane, the priority,
    # which rule was retrieved, and PII safety (in _common). The final lane can still move to human_review when the drafting
    # LLM writes a draft the guard rejects (language, grounding, ...): that is the guard working and it is LLM variance, so
    # the test asserts the routed lane (route trace) and, only when the guard passed, the final lane, citation and actions.
    def routed(r):
        return next(t["lane"] for t in r["trace"] if t.get("node") == "route")

    a = res["PN-100402"]
    assert routed(a) == "draft_for_agent" and a["priority"] == "P1"
    assert any(r["source_id"] == "R-UAE-WPS-2026-07" and r["effective_from"] == "2026-07-15" for r in a["retrieved"])
    if (a.get("guard") or {}).get("passed"):
        assert a["lane"] == "draft_for_agent"
        assert any(c["source_id"] == "R-UAE-WPS-2026-07" and c["effective_from"] == "2026-07-15" for c in a["citations"])
    else:
        assert a["lane"] == "human_review" and "guard_failed->human_review" in a["route_reasons"]    # fail-safe, a person decides
    c = res["PN-101333"]
    assert routed(c) == "auto_answer"
    if (c.get("guard") or {}).get("passed"):
        assert c["lane"] == "auto_answer" and [(x["type"], x["status"]) for x in c["actions"]] == [("reply", "shadow")]
    else:
        assert c["lane"] == "human_review"
