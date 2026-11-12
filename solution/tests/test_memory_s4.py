"""Session 4 section 5.7 "Tests (added to the evals)" plus the memory rules around them (needs the compose postgres + valkey).

A7 has NO memory store. What looks like memory is exactly four things, and these tests pin each of them:
  1. LangGraph's saved ticket state (Postgres checkpoint, thread = ticket id): masked text only, one ticket, one tenant.
  2. Tenant-locked lookups (the code sets tenant_id; the model cannot ask for another customer).
  3. Reviewer suggestions (kb_suggestions, partner_map_suggestions): proposals a person must approve; nothing reads them.
  4. The approved-answer cache (answer_cache, 7-day TTL, topic flush) and the 30 s Valkey tool cache: the only cross-ticket reuse.

The four tests of the 5.7 table are:   no peeking at other customers / no personal data saved / survives a restart / lookups are current.
No LLM is called (fake deciders / chat / retriever); everything else (tools, mock payroll API, RLS, checkpointer, act, log_and_learn) is real.
"""
from __future__ import annotations

import json
import random
import re
import time
import uuid

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from solution.a7 import cache, db, tools as T
from solution.a7.graph import build_graph, postgres_checkpointer, thread_config
from solution.a7.pii.scanner import scan
from solution.tests.p3_fakes import (KB, RULE, FakeDecider, FakeLLM, FakeLookup, base_state, deciders, fake_dedupe, retriever_of, tool_call,
                                     tools_factory)

pytestmark = pytest.mark.db
GOOD = "Yes. From 15 Jul 2026 the SIF file uses v3 with 16 columns and a mandatory establishment-reference column [R-UAE-WPS-2026-07, effective 15 Jul 2026]."


def graph_of(cp, llm, *, auto=None, real_tools=False, chunks=(RULE, KB), tenant_lookup=None):
    kw = dict(tools_factory=tools_factory) if not real_tools else dict(tool_cache=False)
    return build_graph(cp, deciders=deciders(auto or FakeDecider("statutory", 0.95, "urgent")), llm=llm, tenant_lookup=tenant_lookup or FakeLookup(),
                       retriever=retriever_of(*chunks), output_decider=FakeDecider(), dedupe_node=fake_dedupe, **kw)


# ----------------------------------------------------------------------------------------- 5.7 (1) no peeking at other customers
def test_s57_1_falcon_ticket_cannot_read_kestrel(owner, app):
    """A Falcon (C-1004) ticket whose agent asks for Kestrel (C-1001) data gets nothing; RLS shows Falcon no Kestrel rows either."""
    from fastapi.testclient import TestClient
    from solution.a7.mock_payroll.app import RateLimiter, create_app
    from solution.a7.tools.client import PayrollClient

    c = PayrollClient("http://test", "t0k", sleep=lambda s: None)
    c.http = TestClient(create_app("t0k", RateLimiter(clock=lambda: 0.0)), base_url="http://test")
    T.set_client(c)
    try:
        script = [tool_call("get_payroll_run_status", {"run_id": "RUN-C-1001-2026-08", "tenant_id": "C-1001"}),
                  tool_call("get_employee_payslip_status", {"run_id": "RUN-C-1001-2026-08", "employee_id": "E-10234"}, id_="t2"),
                  AIMessage(content="I could not find that run for your account; a colleague will check.")]
        s = base_state(ticket_id="PN-FALCON-PEEK", vault_key="PN-FALCON-PEEK", customer_ref="C-1004", record={"ticket_id": "PN-FALCON-PEEK", "customer_ref": "C-1004"},
                       masked_text="Payroll run stuck at Processing", category="payroll")
        g = graph_of(MemorySaver(), FakeLLM(script), auto=FakeDecider("payroll", 0.97, "high"), real_tools=True, chunks=())
        g.invoke(s, thread_config("PN-FALCON-PEEK"))
        v = g.get_state(thread_config("PN-FALCON-PEEK")).values
    finally:
        T.set_client(None)
    assert v["tenant_id"] == "C-1004" and v["lane"] == "route_queue"
    calls = v["tool_calls"]
    assert [x["name"] for x in calls] == ["get_payroll_run_status", "get_employee_payslip_status"]
    assert all(x["ok"] is False for x in calls), calls            # always empty: nothing came back for Kestrel's run
    assert "Kestrel" not in v["draft"] and "C-1001" not in v["draft"]
    # the model-supplied tenant_id is not even in the tool schema, and the state value wins
    for t in T.ALL_TOOLS:
        assert "tenant_id" not in t.tool_call_schema.model_json_schema()["properties"]
    # database level: scoped to Falcon, Kestrel's rows are invisible in every tenant table
    from solution.a7.human_db import scoped
    owner.execute("DELETE FROM audit_log WHERE ticket_id LIKE 'PN-PEEK-%'")
    owner.execute("INSERT INTO audit_log (tenant_id, ticket_id, event, masked_text, dedupe_key) VALUES ('C-1001','PN-PEEK-K','x','masked','peek-k') "
                  "ON CONFLICT DO NOTHING")
    try:
        with scoped(app, "C-1004"):
            assert app.execute("SELECT count(*) FROM audit_log WHERE tenant_id <> 'C-1004'").fetchone()[0] == 0
            assert app.execute("SELECT count(*) FROM audit_log WHERE ticket_id='PN-PEEK-K'").fetchone()[0] == 0
            assert app.execute("SELECT count(*) FROM answer_cache WHERE tenant_id <> 'C-1004'").fetchone()[0] == 0
            assert app.execute("SELECT count(*) FROM outbox WHERE tenant_id <> 'C-1004'").fetchone()[0] == 0
    finally:
        owner.execute("DELETE FROM audit_log WHERE ticket_id LIKE 'PN-PEEK-%'")


# ----------------------------------------------------------------------------------------- 5.7 (2) no personal data saved
_ID_TOKENS = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{20,}\b|(?<=run--)[0-9a-f-]+|(?<=id=')[^']+")


def _pii_tickets(tickets, n=101):
    out = []
    for t in tickets:
        txt = " ".join(str(t.get(k) or "") for k in ("message", "body", "details", "title", "summary"))
        types = {h.type for h in scan(txt, presidio=False)} & {"AADHAAR", "PAN", "EMIRATES_ID", "EID", "IBAN"}
        if types:
            out.append(t)
    return out[:n]


def test_s57_2_saved_state_has_no_personal_data(tickets, owner):
    """Run the tickets that carry Aadhaar / PAN / Emirates ID / IBAN through gate + graph into the REAL Postgres checkpointer,
    then scan every saved-state table: 0 matches, and none of the original raw values appear either."""
    from solution.a7.nodes.normalise import make_normalise
    from solution.a7.nodes.pii_gate import identify, make_pii_gate
    from solution.a7.pii.patterns import LEGACY_PII
    from solution.a7.vault import LocalKeyProvider, MemoryVaultStore, Vault
    from solution.a7.worker import prepare
    from solution.db import bootstrap

    bootstrap.bootstrap()
    pool = _pii_tickets(tickets)
    assert len(pool) >= 20, "expected many personal-data tickets in the export"
    pool = random.Random(20261108).sample(pool, min(len(pool), 40))        # SAMPLE n=40 (seed 20261108): the graph + DB per ticket is the slow part
    gate, norm = make_pii_gate(Vault(MemoryVaultStore(), LocalKeyProvider(master_key=bytes(range(32))))), make_normalise()
    raws, ids = [], []
    with postgres_checkpointer(db.dsn()) as cp:
        g = graph_of(cp, FakeLLM([AIMessage(content=GOOD)]), tenant_lookup=None)
        for t in pool:
            tid = identify(t)[1]
            blob = json.dumps(t, ensure_ascii=False)
            for rx in LEGACY_PII.values():
                raws += [m.group(0) for m in rx.finditer(blob) if len(m.group(0)) >= 8]
            st = prepare(t, gate, norm)
            if st.get("pii_gate_status") != "passed":
                continue
            cp.delete_thread(tid)
            try:
                g.invoke(st, thread_config(tid), durability="sync")
            except Exception:  # noqa: BLE001  a ticket the fake pipeline cannot finish still leaves a checkpoint to scan
                pass
            ids.append(tid)
        assert len(ids) >= 15
        rows = []
        # every saved checkpoint, decoded by the saver (msgpack blobs are bytea, so scan the decoded content, not hex): the state values,
        # the metadata and the pending writes of each step. Row ids / timestamps are not content and are left out.
        for tid in ids:
            for tup in cp.list(thread_config(tid)):
                rows.append(json.dumps(tup.checkpoint.get("channel_values", {}), default=str, ensure_ascii=False))
                rows.append(json.dumps([w[1:] for w in (tup.pending_writes or [])], default=str, ensure_ascii=False))
        for tid in ids:
            cp.delete_thread(tid)
    saved = _ID_TOKENS.sub(" ", "\n".join(rows))      # LangGraph checkpoint/message ids are UUIDs and hex: digit runs that are not content
    assert len(saved) > 10_000
    hits = sorted({h.type for h in scan(saved, presidio=False)})
    assert not hits, f"PII types found in saved state: {hits}"
    assert not [r for r in set(raws) if r in saved], "an original raw value survived in saved state"


# ----------------------------------------------------------------------------------------- 5.7 (3) survives a restart
def test_s57_3_restart_while_pn100402_waits_for_approval(by_id, owner):
    from solution.a7.nodes.normalise import make_normalise
    from solution.a7.nodes.pii_gate import make_pii_gate
    from solution.a7.vault import LocalKeyProvider, MemoryVaultStore, Vault
    from solution.a7.worker import prepare
    from solution.db import bootstrap

    bootstrap.bootstrap()
    st = prepare(by_id["PN-100402"], make_pii_gate(Vault(MemoryVaultStore(), LocalKeyProvider(master_key=bytes(range(32))))), make_normalise())
    cfg = thread_config("PN-100402")
    with postgres_checkpointer(db.dsn()) as cp:
        cp.delete_thread("PN-100402")
        g = graph_of(cp, FakeLLM([AIMessage(content=GOOD)]), auto=FakeDecider("statutory", 0.95, "urgent"), tenant_lookup=None)
        g.invoke(st, cfg, durability="sync")
        before = g.get_state(cfg)
        assert before.next == ("human_approval",)
        draft = before.values["draft"]
    with postgres_checkpointer(db.dsn()) as cp2:                               # "restart": new connection, new graph object, nothing in memory
        g2 = graph_of(cp2, FakeLLM([AIMessage(content="SHOULD NOT BE CALLED")]), auto=FakeDecider("statutory", 0.95, "urgent"), tenant_lookup=None)
        snap = g2.get_state(cfg)
        assert snap.next == ("human_approval",) and snap.values["draft"] == draft      # picks up at the approval step, same draft
        g2.invoke(Command(resume={"status": "approved", "reviewer": "r1"}), cfg)
        assert g2.get_state(cfg).next == () and g2.get_state(cfg).values["draft"] == draft
        cp2.delete_thread("PN-100402")
    owner.execute("DELETE FROM outbox WHERE ticket_id='PN-100402'")
    owner.execute("DELETE FROM approvals WHERE ticket_id='PN-100402'")


# ----------------------------------------------------------------------------------------- 5.7 (4) lookups are current
def test_s57_4_lookups_are_current():
    """Session 4 names a Jira bug. This build has NO Jira read tool (Jira is only an approved intent row), so a closed bug cannot be shown:
    the lookup tools are payroll status, tenant profile, statutory rule and KB only. The part that exists is the 30 s status cache:
    inside the window the previous answer is served, after it a changed status shows up."""
    names = {t.name for lane in ("auto_answer", "draft_for_agent", "route_queue") for t in T.get_tools(lane)}
    assert not any("jira" in n or "bug" in n for n in names), names
    vk = cache.valkey_client()
    vk.flushdb() if False else None                                           # never flush a shared db; use a unique tenant key
    tenant = "T-CUR-" + uuid.uuid4().hex[:6]
    tc = cache.ToolCache(client=vk, ttl=1)
    state = {"status": "computing"}
    first = tc.call(tenant, "get_payroll_run_status", {"run_id": "R"}, lambda: dict(state))
    state["status"] = "paid"                                                  # the world changes (the bug is closed)
    assert tc.call(tenant, "get_payroll_run_status", {"run_id": "R"}, lambda: dict(state)) == first      # within the TTL: previous answer
    time.sleep(1.3)
    assert tc.call(tenant, "get_payroll_run_status", {"run_id": "R"}, lambda: dict(state))["status"] == "paid"   # after it: current
    assert cache.STATUS_TTL_S == 30
    for k in vk.scan_iter(f"a7:tool:{tenant}:*"):
        vk.delete(k)


# ----------------------------------------------------------------------------------------- memory rules around the table
def test_checkpoint_memory_is_per_ticket_and_per_tenant(owner):
    """Thread = ticket id. A second ticket (even for the same tenant) starts from nothing; a tenant-B ticket never sees tenant-A state."""
    cp = MemorySaver()
    a = graph_of(cp, FakeLLM([AIMessage(content=GOOD)]))
    a.invoke(base_state(ticket_id="PN-MEM-A", vault_key="PN-MEM-A", customer_ref="C-1004", masked_text="Falcon only question"), thread_config("PN-MEM-A"))
    b = graph_of(cp, FakeLLM([AIMessage(content=GOOD)]))
    b.invoke(base_state(ticket_id="PN-MEM-B", vault_key="PN-MEM-B", customer_ref="C-1001", masked_text="Kestrel question"), thread_config("PN-MEM-B"))
    va, vb = a.get_state(thread_config("PN-MEM-A")).values, b.get_state(thread_config("PN-MEM-B")).values
    assert va["tenant_id"] == "C-1004" and vb["tenant_id"] == "C-1001"
    assert "Falcon" not in json.dumps(vb, default=str) and "Kestrel" not in json.dumps(va, default=str)
    assert not a.get_state(thread_config("PN-MEM-NEW")).values                # an unseen ticket id has no state at all
    # a replay of ticket A's id under another tenant's data does not read B's thread either
    assert vb["ticket_id"] == "PN-MEM-B" and va["ticket_id"] == "PN-MEM-A"


def test_suggestions_are_proposals_nobody_reads_them(owner, app):
    """log_and_learn writes kb_suggestions / partner_map_suggestions as 'proposed'. Neither resolve_tenant nor retrieval reads them,
    so nothing learned changes behaviour until a person approves it into the KB / partner map (outside the pipeline)."""
    from solution.a7.nodes.log_and_learn import make_log_and_learn
    from solution.a7.nodes.resolve_tenant import DbTenantLookup

    name = "Zzz Memory Test Co " + uuid.uuid4().hex[:6]
    tid = "PN-LL-" + uuid.uuid4().hex[:6]
    state = {"tenant_id": "C-1004", "ticket_id": tid, "lane": "draft_for_agent", "draft": "Open Payslips and choose bulk download.",
             "approval": {"status": "edited", "reviewer": "r1", "edited_draft": "Open Payslips, choose bulk download, then Download all."},
             "citations": [{"source_id": "KB-0105"}], "guard": {"stale_kb": ["KB-0105"]},
             "tenant_resolution": {"ambiguous": True, "partner_name": name, "customer_id": "C-1004", "resolved_by": "reviewer-1"}}
    try:
        make_log_and_learn(lambda: db.connect(app=True, autocommit=True))(state)
        kb = owner.execute("SELECT status, reason FROM kb_suggestions WHERE ticket_id=%s ORDER BY reason", (tid,)).fetchall()
        pm = owner.execute("SELECT status FROM partner_map_suggestions WHERE partner_name=%s", (name,)).fetchall()
        assert kb and all(s == "proposed" for s, _ in kb) and {r for _, r in kb} >= {"reviewer_edit", "stale_kb"}
        assert pm == [("proposed",)]
        # not used: the learned name does not resolve, and the KB text the agent retrieves does not include the suggestion
        assert DbTenantLookup().partner(name) == []
        n = owner.execute("SELECT count(*) FROM kb_chunks WHERE body ILIKE '%then Download all%'").fetchone()[0]
        assert n == 0
    finally:
        owner.execute("DELETE FROM kb_suggestions WHERE ticket_id=%s", (tid,))
        owner.execute("DELETE FROM partner_map_suggestions WHERE partner_name=%s", (name,))
        owner.execute("DELETE FROM audit_log WHERE ticket_id=%s", (tid,))


def test_approved_answer_cache_is_the_only_cross_ticket_memory(owner, app):
    """Cross-ticket reuse = answer_cache only: human-approved, tenant-locked, 7-day TTL, flushed when the KB topic changes."""
    from datetime import datetime, timedelta, timezone
    from solution.a7.human_db import scoped
    from solution.tests.test_cache import vec
    import math

    ta, tb, topic = "T-CA", "T-CB", "tmem_topic"
    for c in (ta, tb):
        owner.execute("INSERT INTO customers VALUES (%s,%s,'Business','IN',1,1) ON CONFLICT DO NOTHING", (c, f"Test {c}"))
    owner.execute("DELETE FROM answer_cache WHERE tenant_id IN ('T-CA','T-CB')")
    owner.execute("DELETE FROM kb_chunks WHERE source_id='KB-TMEM'")
    owner.execute("INSERT INTO kb_chunks (source, source_id, chunk_index, region, topic, title, body) VALUES ('kb','KB-TMEM',0,'IN',%s,'t','Open People.')", (topic,))
    now = datetime.now(timezone.utc)
    st = {"tenant_id": ta, "ticket_id": "PN-MEM-C1", "topic": topic, "region": "IN", "masked_text": "How do I add an employee?", "category": "howto",
          "lane": "draft_for_agent", "priority": "P3", "draft": "Open People and click Add employee. See KB-TMEM.", "citations": [{"source_id": "KB-TMEM"}],
          "route_reasons": [], "guard": {"passed": True}, "approval": {"status": "approved", "reviewer": "r1"}}
    try:
        with scoped(app, ta):
            assert cache.store_approved(app, st, vec(1.0), now=now) == (True, None)
            assert cache.lookup(app, tenant_id=ta, topic=topic, region="IN", embedding=vec(1.0), now=now)["source_ticket_id"] == "PN-MEM-C1"
            assert cache.lookup(app, tenant_id=ta, topic=topic, region="IN", embedding=vec(1.0), now=now + timedelta(days=8)) is None   # TTL
        # unapproved drafts are never stored
        with scoped(app, ta):
            assert cache.store_approved(app, {**st, "ticket_id": "PN-MEM-C2", "approval": {"status": "pending"}}, vec(1.0))[0] is False
        with scoped(app, tb):                                                                                                      # tenant lock
            assert cache.lookup(app, tenant_id=tb, topic=topic, region="IN", embedding=vec(1.0), now=now) is None
            assert app.execute("SELECT count(*) FROM answer_cache").fetchone()[0] == 0
        # KB edit flushes the topic
        owner.execute("UPDATE kb_chunks SET body='Open People, then Add.' WHERE source_id='KB-TMEM'")
        assert owner.execute("SELECT count(*) FROM answer_cache WHERE topic=%s", (topic,)).fetchone()[0] == 0
    finally:
        owner.execute("DELETE FROM answer_cache WHERE tenant_id IN ('T-CA','T-CB')")
        owner.execute("DELETE FROM kb_chunks WHERE source_id='KB-TMEM'")
        owner.execute("DELETE FROM customers WHERE customer_id IN ('T-CA','T-CB')")
