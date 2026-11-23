"""Gate: 0 PII leaks after a run (Session 4 s4.1).

Scans, with Presidio + the regex/legacy layers, everything a run leaves behind: harvest drafts and ticket text, outbox, approvals, audit
log, answer_cache, escalations, shadow reviews, LangGraph checkpoint tables, and Valkey (tool cache). Also checks "canaries": the real
PII values (IBAN, Emirates ID, PAN, phone, email ...) found in the RAW source records of the harvested tickets must not occur literally
in any store. One tool-using ticket (PN-101055) is run live first so the Valkey tool cache actually holds entries (30 s TTL) when scanned.
pii_vault is excluded on purpose: it holds ciphertext only. Only counts and types are reported.
"""
from __future__ import annotations

import pytest

from solution.evals import results
from solution.evals.pii_stores import canaries, gather, raw_strings, scan_stores

pytestmark = pytest.mark.eval
LIVE_TOOL_TICKET = "PN-101055"


@pytest.fixture(scope="module")
def live_valkey_run():
    """Run one tool-using ticket through the real graph and return gathered stores while the tool cache is still warm."""
    from solution.evals.p5_harvest import start_mock
    from solution.a7 import db
    from solution.a7.decide.base import get_decider
    from solution.a7.graph import build_graph, postgres_checkpointer
    from solution.a7.nodes.normalise import make_normalise
    from solution.a7.nodes.pii_gate import make_pii_gate
    from solution.a7.vault import LocalKeyProvider, PostgresVaultStore, Vault
    from solution.a7.worker import find_record, run_ticket
    from solution.evals import spend

    spend.guard()
    mock = start_mock()
    try:
        nova, qwen = get_decider("bedrock_nova"), get_decider("bedrock_qwen32b")
        with db.connect(autocommit=True) as vconn, postgres_checkpointer() as cp:
            gate = make_pii_gate(Vault(PostgresVaultStore(vconn), LocalKeyProvider()))
            graph = build_graph(cp, deciders={"bedrock_nova": nova, "bedrock_qwen32b": qwen}, output_decider=nova)
            cp.delete_thread(LIVE_TOOL_TICKET)
            v = run_ticket(find_record(LIVE_TOOL_TICKET), graph, gate, make_normalise())
            spend.add("pii_live_ticket", nova.usage()["cost_usd"] + qwen.usage()["cost_usd"] + sum(
                x["usage"]["cost_usd"] for x in v.get("trace", []) if x.get("node") == "agent" and "usage" in x))
        return gather(), v
    finally:
        mock.terminate()
        with db.connect(autocommit=True) as c:      # leave no vault rows behind (solution/tests vault tests assume their own key)
            c.execute("DELETE FROM pii_vault WHERE ticket_id = %s", (LIVE_TOOL_TICKET,))


def test_zero_pii_leaks_after_run(harvest, live_valkey_run):
    from solution.a7.worker import find_record

    stores, live = live_valkey_run
    stores["harvest.masked_text"] = [r["masked_text"] or "" for r in harvest]
    stores["harvest.draft"] = [r["draft"] or "" for r in harvest]
    stores["harvest.contexts"] = [c["body"] for r in harvest for c in r["contexts"]]
    ids = {r["id"] for r in harvest} | {LIVE_TOOL_TICKET}
    can = canaries([s for i in sorted(ids) for s in raw_strings(find_record(i))])
    rep = scan_stores(stores, can)
    keys = rep["fragments"]["valkey"]
    results.record("pii", "PII leaks in drafts, outbox, approvals, audit, answer_cache, checkpoints, Valkey", "0",
                   f"{rep['leaks']} leaks ({sum(rep['canary_hits'].values())} canary, {sum(sum(v.values()) for v in rep['scanner_hits'].values())} scanner)",
                   f"n={len(harvest)} harvested tickets + 1 live tool ticket; {rep['canaries_checked']} raw PII values checked as canaries; "
                   f"{sum(rep['fragments'].values())} text fragments across {len(rep['fragments'])} stores ({keys} Valkey strings)",
                   rep["leaks"] == 0, f"by store: scanner {rep['scanner_hits']} canary {rep['canary_hits']}" if rep["leaks"] else f"Scanner = Presidio + regex/legacy layers, NER off for names. UUID / long-hex ids (LangGraph checkpoint and run ids) are masked by solution.a7.pii.scanner.mask_ids before scanning: {sum(rep['id_spans_masked'].values())} id spans masked.")
    assert rep["leaks"] == 0, (rep["scanner_hits"], rep["canary_hits"])


def test_valkey_was_populated(live_valkey_run):
    stores, live = live_valkey_run
    assert any(name == "get_payroll_run_status" or name == "list_payroll_runs" for name, _ in [(c["name"], c["ok"]) for c in live.get("tool_calls") or []]) \
        or len(stores["valkey"]) > 0, "no tool call and no Valkey key: the Valkey scan would be vacuous"
