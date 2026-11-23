"""Phase 5 A follow-ups: (3) statutory_no_rule_cited only when a rule is in force for region/topic; (4) retrieve adds in-force rules for
statutory tickets; (5) UUID/hex-aware PII scan that still catches real canaries."""
from __future__ import annotations

import pytest

from solution.a7.nodes.output_guard import code_checks
from solution.a7.nodes.retrieve import make_retrieve
from solution.tests.p3_fakes import PF
from solution.tests.test_p3_nodes import agent_state

KB142 = {"source_id": "KB-0142", "source": "kb", "title": "PF: wage ceiling and contribution calculation", "effective_from": None,
         "effective_to": "2026-07-31", "score": 0.03, "body": "PF wage ceiling: contributions are calculated on basic wages up to INR 15,000 per month."}
R_PF = {"source_id": "R-IN-PF-2026-08", "kind": "rule", "effective_from": "2026-08-01", "effective_to": None, "score": 1}
K_PF = {"source_id": "KB-0142", "kind": "kb", "effective_from": None, "effective_to": "2026-07-31", "score": 1}


def pf_state(draft, *, as_of, retrieved, chunks, topic_rules):
    return agent_state(draft=draft, category="statutory", language="en", tenant_region="IN", as_of=as_of, retrieved=retrieved,
                       context_chunks=chunks, topic_rules=topic_rules, masked_text="PF figures differ from our CA's computation.")


def test_pre_change_pf_ticket_with_kb_citation_passes():
    st = pf_state("The PF wage ceiling is INR 15,000 per month [KB-0142].", as_of="2026-07-04", retrieved=[K_PF], chunks=[KB142], topic_rules=[])
    assert code_checks(st) == []


def test_post_change_pf_ticket_without_the_rule_fails():
    st = pf_state("The PF wage ceiling is INR 25,000 per month [KB-0150].", as_of="2026-08-30",
                  retrieved=[{**R_PF}, {"source_id": "KB-0150", "kind": "kb", "effective_from": None, "effective_to": None, "score": 1}],
                  chunks=[PF, {"source_id": "KB-0150", "body": "TDS slabs 25,000"}], topic_rules=["R-IN-PF-2026-08"])
    assert "statutory_no_rule_cited" in code_checks(st)


def test_post_change_pf_ticket_with_the_rule_passes():
    st = pf_state("From 1 August 2026 the EPF wage ceiling is INR 25,000 per month (R-IN-PF-2026-08, effective 1 August 2026).",
                  as_of="2026-08-30", retrieved=[R_PF], chunks=[PF], topic_rules=["R-IN-PF-2026-08"])
    assert code_checks(st) == []


def test_pre_change_ticket_still_cannot_cite_the_future_rule_as_current():
    st = pf_state("The EPF wage ceiling is INR 25,000 per month (R-IN-PF-2026-08, effective 1 August 2026).", as_of="2026-07-04",
                  retrieved=[K_PF], chunks=[KB142], topic_rules=[])
    fails = code_checks(st)
    assert "cited_not_retrieved:R-IN-PF-2026-08" in fails and any(f.startswith("numbers_not_grounded") for f in fails)


def test_no_topic_rules_key_fails_closed():
    st = pf_state("The PF wage ceiling is INR 15,000 per month [KB-0142].", as_of="2026-07-04", retrieved=[K_PF], chunks=[KB142], topic_rules=None)
    assert "statutory_no_rule_cited" in code_checks(st)


# ---- retrieve (needs the compose DB with reference + KB loaded; keyword-only path, no embeddings) -------------------------
@pytest.mark.db
@pytest.mark.parametrize("received,expect_rule", [("2026-08-30T10:00:00Z", True), ("2026-07-04T10:00:00Z", False)])
def test_retrieve_adds_in_force_rules_for_statutory_tickets(owner, received, expect_rule):
    def no_embed(*a, **k):
        raise RuntimeError("offline")
    from solution.a7.nodes.retrieve import db_retriever
    tenant = owner.execute("select customer_id from customers where region = 'IN' order by 1 limit 1").fetchone()[0]
    node = make_retrieve(db_retriever(embedder=no_embed))
    st = {"tenant_id": tenant, "tenant_region": "IN", "received_at": received, "category": "statutory",
          "masked_text": "Provident fund wage ceiling: which contribution limit applies to our employees?", "trace": []}
    out = node(st)
    ids = [r["source_id"] for r in out["retrieved"]]
    assert out["ticket_topic"] == "pf_wage_ceiling"
    assert ("R-IN-PF-2026-08" in ids) == expect_rule
    assert out["topic_rules"] == (["R-IN-PF-2026-08"] if expect_rule else [])
    assert "R-IN-TDS-2026-04" not in out["topic_rules"]


# ---- PII scan: UUID / hex ids are masked, real identifiers are still found ---------------------------------------------------
def test_uuid_and_hex_ids_are_not_pii_but_canaries_are():
    from solution.a7.pii.scanner import mask_ids, scan

    def scan_text(t):
        return scan(t, presidio=True, ner=False)
    uuid_with_phone_look = "thread 1f0d3c2a-7b9e-4c11-9a62-123456789012 checkpoint 0123456789abcdef0123456789abcdef"
    masked = mask_ids(uuid_with_phone_look)
    assert len(masked) == len(uuid_with_phone_look) and masked.startswith("thread ____") and masked.count("_") == 36 + 32
    assert scan_text(uuid_with_phone_look) == []
    canaries = {
        "AADHAAR": "Aadhaar 2345 6789 0123 on file",
        "PAN": "PAN ABCDE1234F please verify",
        "IBAN": "IBAN AE070331234567890123456 for salary",
        "EMIRATES_ID": "Emirates ID 784-1990-1234567-1",
    }
    for name, text in canaries.items():
        assert scan_text(f"{text} and id 1f0d3c2a-7b9e-4c11-9a62-123456789012"), name


def test_mask_ids_checkpoint_shapes():
    """Integration: ids packed into msgpack checkpoints have no delimiter after them; long digit strings are not ids."""
    from solution.a7.pii.scanner import mask_ids
    u = "1f0b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d"
    assert mask_ids("$" + u + "abc") == "$" + "_" * 36 + "abc"
    h = "5a3b9c0d1e2f4a5b6c7d8e9f0a1b2c3dnode_x"
    assert mask_ids(h).startswith("_" * 32) and "node_x" not in mask_ids(h)[:32]
    assert mask_ids("123456789012345678901234567890") == "123456789012345678901234567890"
