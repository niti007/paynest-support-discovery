"""Phase 3C: human_approval, act, log_and_learn, escalation timers, DB roles, reviewer API (needs postgres).

Env: A7_DATABASE_URL (owner), A7_APP_DATABASE_URL (app_user), A7_ACT_DATABASE_URL (a7_act) point at the same DB.
"""
import datetime as dt
import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from solution.a7 import db, escalation
from solution.a7.api.app import create_app
from solution.a7.human_db import act_dsn, connect_act
from solution.a7.nodes.act import ActRefused, make_act, render_outbound
from solution.a7.nodes.log_and_learn import AuditPIIError, log_and_learn
from solution.a7.vault import LocalKeyProvider, PostgresVaultStore, Vault
from solution.db import bootstrap
from synth import iban
from _p3c_stub_graph import build_stub_graph

pytestmark = pytest.mark.db
UTC = dt.timezone.utc
ENT, BUS, STD = "T-ENT", "T-BUS", "T-STD"
TOKEN = "test-token"
TABLES = "approvals, escalations, outbox, audit_log, incidents, kb_suggestions, partner_map_suggestions"


@pytest.fixture(scope="module", autouse=True)
def setup(owner):
    bootstrap.bootstrap()
    for cid, tier in ((ENT, "Enterprise"), (BUS, "Business"), (STD, "Standard")):
        owner.execute("INSERT INTO customers VALUES (%s,%s,%s,'IN',1,1) ON CONFLICT DO NOTHING", (cid, f"Test {cid}", tier))
    yield
    owner.execute(f"TRUNCATE {TABLES}")
    owner.execute("DELETE FROM customers WHERE customer_id LIKE 'T-%'")


@pytest.fixture(autouse=True)
def clean(owner):
    owner.execute(f"TRUNCATE {TABLES}")


def tid():
    return "TST-" + uuid.uuid4().hex[:8]


def base(lane="draft_for_agent", tenant=ENT, **kw):
    s = dict(ticket_id=tid(), tenant_id=tenant, lane=lane, priority="P2", category="statutory", language="en",
             received_at="2026-09-10T08:00:00Z", draft="Per rule R-UAE-WPS-2026-07 the cut-off is 15 Jul 2026.",
             citations=["R-UAE-WPS-2026-07"], guard={"ok": True}, route_reasons=["rule:stale_kb"], tool_calls=[])
    s.update(kw)
    return s


def rows(owner, sql, *p):
    return owner.execute(sql, p).fetchall()


# ---------------------------------------------------------------- (b) roles
def test_b_app_user_cannot_read_vault_but_act_can(owner):
    owner.execute("INSERT INTO pii_vault VALUES ('TST-V','<IBAN_1>','IBAN',%s,'local:x') ON CONFLICT DO NOTHING", (b"\x01",))
    with db.connect(app=True, autocommit=True) as app:
        for q in ("SELECT * FROM pii_vault", "SELECT ciphertext FROM pii_vault", "SELECT count(*) FROM pii_vault"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(q)
        app.execute("INSERT INTO pii_vault VALUES ('TST-V2','<IBAN_1>','IBAN',%s,'local:x') ON CONFLICT DO NOTHING", (b"\x02",))
    with connect_act() as a:
        assert a.execute("SELECT count(*) FROM pii_vault WHERE ticket_id='TST-V'").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            a.execute("SELECT * FROM outbox")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            a.execute("INSERT INTO pii_vault VALUES ('x','y','z',%s,'k')", (b"\x00",))
    owner.execute("DELETE FROM pii_vault WHERE ticket_id LIKE 'TST-V%'")


def test_b2_gate_via_app_user_then_act_reveals(by_id, owner):
    from solution.a7.nodes.pii_gate import make_pii_gate
    keys = LocalKeyProvider(master_key=bytes(range(32)))
    with db.connect(app=True, autocommit=True) as app, connect_act() as a:
        out = make_pii_gate(Vault(PostgresVaultStore(app), keys))({"raw_record": by_id["PN-101333"]})
        body = f"Please confirm the account {out['pii_placeholders'] and '<IBAN_1>'}."
        shown = render_outbound(body, out["vault_key"], vault=Vault(PostgresVaultStore(a), keys))
        assert "<IBAN_1>" not in shown and "IBAN_1" not in shown
        assert render_outbound(body, "x", allow=False) == body
    owner.execute("DELETE FROM pii_vault WHERE ticket_id = %s", (out["vault_key"],))


# ---------------------------------------------------------------- (c) act refusals
def test_c_act_refused_without_approval(owner):
    act = make_act()
    for ap in (None, {"status": "pending"}, {}):
        with pytest.raises(ActRefused):
            act(base(approval=ap))
    for lane in ("incident", "human_review"):
        with pytest.raises(ActRefused):
            act(base(lane=lane, approval={"status": "pending"}))
    assert rows(owner, "SELECT count(*) FROM outbox")[0][0] == 0
    # rejected: no actions, no outbox
    r = act(base(approval={"status": "rejected", "reviewer": "r1"}))
    assert r["actions"][0]["status"] == "skipped" and rows(owner, "SELECT count(*) FROM outbox")[0][0] == 0
    # block: refused, nothing
    r = act(base(lane="block", approval={"status": "approved", "reviewer": "r1"}))
    assert r["actions"][0]["status"] == "refused" and rows(owner, "SELECT count(*) FROM outbox")[0][0] == 0
    # approved draft_for_agent: a draft row for a human, never sent
    s = base(approval={"status": "edited", "reviewer": "r1", "edited_draft": "Edited text."})
    r = act(s)
    row = rows(owner, "SELECT kind, status, masked_body, restore_pii, sent_at, approved_by FROM outbox")[0]
    assert row == ("draft", "awaiting_agent_send", "Edited text.", True, None, "r1")


def test_c2_intents_need_approval_and_never_automated(owner):
    act = make_act()
    props = [{"type": "refund", "status": "proposed", "summary": "refund INR 500"},
             {"type": "payroll_write", "status": "proposed", "summary": "x"},
             {"type": "attachment", "status": "proposed"}]
    r = act(base(lane="route_queue", queue="Payroll-Ops", actions=props))["actions"]
    assert [a["status"] for a in r] == ["queued", "refused", "refused", "refused"]    # refund refused: no approval on route_queue
    owner.execute("TRUNCATE outbox")
    r = act(base(approval={"status": "approved", "reviewer": "r1"}, actions=props))["actions"]
    by = {a["type"]: a["status"] for a in r}
    assert by["refund"] == "intent_recorded" and by["payroll_write"] == "refused" and by["attachment"] == "refused"
    assert rows(owner, "SELECT kind, status FROM outbox WHERE kind <> 'draft'") == [("refund_intent", "awaiting_execution")]


def test_c3_incident_one_jira_intent(owner):
    act = make_act()
    for _ in range(2):    # re-run is idempotent
        r = act(base(lane="incident", priority="P1", category="payroll", approval={"status": "approved", "reviewer": "r1"},
                     ticket_id="TST-INC1"))["actions"]
    kinds = sorted(k for (k,) in rows(owner, "SELECT kind FROM outbox"))
    assert kinds == ["jira_intent", "notify"]
    assert rows(owner, "SELECT ticket_ids FROM incidents") == [(["TST-INC1"],)]


# ---------------------------------------------------------------- (d) shadow
def test_d_auto_answer_shadow(owner, monkeypatch):
    monkeypatch.delenv("A7_AUTO_SEND", raising=False)
    act = make_act()
    s = base(lane="auto_answer", priority="P3", category="howto", draft="See KB-0105 to add an employee.")
    r = act(s)["actions"][0]
    assert r["shadow"] is True
    row = rows(owner, "SELECT shadow, status, sent_at, restore_pii, lane FROM outbox")
    assert row == [(True, "shadow", None, False, "auto_answer")]
    act(s)                                                   # idempotent
    assert rows(owner, "SELECT count(*) FROM outbox")[0][0] == 1
    # P1 and failed guard are never auto-answered
    owner.execute("TRUNCATE outbox")
    assert act(base(lane="auto_answer", priority="P1"))["actions"][0]["status"] == "refused"
    assert act(base(lane="auto_answer", guard={"ok": False}))["actions"][0]["status"] == "refused"
    assert rows(owner, "SELECT count(*) FROM outbox")[0][0] == 0
    # unmasked PII in a reply is refused
    with pytest.raises(ActRefused):
        act(base(lane="auto_answer", draft=f"Your account {iban(3)}"))
    # kill switch semantics: AUTO_SEND=true is queued (still never sent by this code)
    monkeypatch.setenv("A7_AUTO_SEND", "true")
    act(base(lane="auto_answer", priority="P3"))
    assert rows(owner, "SELECT shadow, status, sent_at FROM outbox") == [(False, "queued", None)]


# ---------------------------------------------------------------- (e) audit rejects PII
def test_e_audit_rejects_pii(owner):
    s = base(approval={"status": "approved", "reviewer": "r1"}, draft=f"Refund to {iban(5)} done")
    with pytest.raises(AuditPIIError):
        log_and_learn(s)
    s = base(approval={"status": "approved", "reviewer": "r1"}, audit=[{"event": "x", "text": "mail a@b.com"}])
    with pytest.raises(AuditPIIError):
        log_and_learn(s)
    assert rows(owner, "SELECT count(*) FROM audit_log")[0][0] == 0


def test_e2_audit_and_learning(owner):
    s = base(approval={"status": "edited", "reviewer": "r1", "edited_draft": "Better wording <IBAN_1>."},
             guard={"ok": True, "stale_kb": ["KB-0202"]}, ticket_id="TST-L1",
             tenant_resolution={"ambiguous": True, "partner_name": "Falcon Trading LLC", "customer_id": ENT, "resolved_by": "r1"},
             actions=[{"type": "draft", "status": "awaiting_agent_send", "outbox_id": 1}])
    log_and_learn(s)
    log_and_learn(s)                                       # idempotent
    assert rows(owner, "SELECT count(*), min(masked_text) FROM audit_log") == [(1, "Better wording <IBAN_1>.")]
    assert sorted(rows(owner, "SELECT reason, source_id FROM kb_suggestions")) == [
        ("reviewer_edit", "R-UAE-WPS-2026-07"), ("stale_kb", "KB-0202")]
    assert rows(owner, "SELECT partner_name, customer_id, status FROM partner_map_suggestions") == [("Falcon Trading LLC", ENT, "proposed")]


# ---------------------------------------------------------------- (f) timers
def table(owner):
    return {k: d for k, d in rows(owner, "SELECT kind, due_at FROM escalations")}


def test_f_timers_and_run_due(owner):
    # P1, Enterprise, payroll week, privacy concern, received 04:00Z
    s = base(priority="P1", is_payroll_window=True, received_at="2026-09-26T04:00:00Z", privacy_concern=True, ticket_id="TST-T1")
    with db.connect(app=True, autocommit=True) as app:
        escalation.create_timers(app, s)
        escalation.create_timers(app, s)                    # idempotent
    t = table(owner)
    d = lambda h, m: dt.datetime(2026, 9, 26, h, m, tzinfo=UTC)
    assert t == {"l1_to_l2": d(5, 0), "privacy_dpo_security": d(5, 0), "p1_first_response": d(4, 30)}
    # other tiers / priorities
    with db.connect(app=True, autocommit=True) as app:
        escalation.create_timers(app, base(priority="P1", tenant=BUS, received_at="2026-09-10T08:00:00Z", ticket_id="TST-T2"))
        escalation.create_timers(app, base(priority="P1", tenant=STD, ticket_id="TST-T3"))
        escalation.create_timers(app, base(priority="P2", tenant=ENT, received_at="2026-09-10T08:00:00Z", ticket_id="TST-T4"))
    got = {(a, k): due for a, k, due in rows(owner, "SELECT ticket_id, kind, due_at FROM escalations")}
    assert got[("TST-T2", "l1_to_l2")] == dt.datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    assert got[("TST-T2", "p1_first_response")] == dt.datetime(2026, 9, 10, 10, 0, tzinfo=UTC)     # Business 2 h
    assert ("TST-T3", "p1_first_response") not in got                                              # Standard: best effort
    assert got[("TST-T4", "l1_to_l2")] == dt.datetime(2026, 9, 10, 12, 0, tzinfo=UTC)              # 4 h
    assert ("TST-T4", "p1_first_response") not in got
    owner.execute("DELETE FROM escalations WHERE ticket_id <> 'TST-T1'")

    assert escalation.run_due(d(4, 29)) == []
    first = escalation.run_due(d(4, 30))
    assert len(first) == 1
    assert len(escalation.run_due(d(5, 1))) == 2
    assert escalation.run_due(d(9, 0)) == []                # fires once only
    assert rows(owner, "SELECT count(*) FROM audit_log WHERE event='escalation_fired'")[0][0] == 3
    assert rows(owner, "SELECT count(*) FROM outbox WHERE kind='notify'")[0][0] == 3
    assert rows(owner, "SELECT count(*) FROM escalations WHERE fired_at IS NULL")[0][0] == 0
    # the scheduler entry shares the path and is idempotent
    eid_ = rows(owner, "SELECT id FROM escalations LIMIT 1")[0][0]
    assert escalation.handler({"escalation_id": eid_}) == {"fired": []}


# ---------------------------------------------------------------- stub graph + API
@pytest.fixture()
def pool():
    p = ConnectionPool(db.app_dsn(), min_size=1, max_size=4, open=True,
                       kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0})
    yield p
    p.close()


def start(pool, state):
    g = build_stub_graph(PostgresSaver(pool))
    cfg = {"configurable": {"thread_id": state["ticket_id"]}}
    out = g.invoke(state, cfg, durability="sync")
    assert out["__interrupt__"]
    return out


@pytest.fixture()
def client():
    app = create_app(graph_factory=build_stub_graph, token=TOKEN)
    with TestClient(app) as c:
        yield c


H = {"Authorization": f"Bearer {TOKEN}", "X-Tenant-Id": ENT}


def test_a_restart_during_approval_resumes_same_draft(pool, owner):
    s = base(priority="P1", ticket_id="TST-R1", draft="Draft v1 citing R-UAE-WPS-2026-07 (15 Jul 2026).")
    out = start(pool, s)
    assert out["__interrupt__"][0].value["draft"] == s["draft"]
    n_esc = rows(owner, "SELECT count(*) FROM escalations")[0][0]
    pool.close()                                           # "crash": pool and graph object gone
    del out
    app = create_app(graph_factory=build_stub_graph, token=TOKEN)    # fresh process: new pool, new graph from PostgresSaver
    with TestClient(app) as c:
        assert c.get("/tickets/TST-R1", headers=H).json()["status"] == "pending"
        r = c.post("/tickets/TST-R1/decision", headers=H, json={"status": "approved", "reviewer": "rev1"})
        assert r.status_code == 200, r.text
        assert r.json()["actions"][0]["type"] == "draft"
        assert c.post("/tickets/TST-R1/decision", headers=H, json={"status": "approved", "reviewer": "rev1"}).status_code == 409
    assert rows(owner, "SELECT masked_body, approved_by, status FROM outbox") == [(s["draft"], "rev1", "awaiting_agent_send")]
    assert rows(owner, "SELECT masked_text FROM audit_log WHERE event='ticket_processed'") == [(s["draft"],)]
    assert rows(owner, "SELECT count(*) FROM escalations")[0][0] == n_esc          # timers not duplicated by the re-run
    assert rows(owner, "SELECT count(*) FROM escalations WHERE resolved_at IS NOT NULL")[0][0] >= 1
    assert rows(owner, "SELECT status, reviewer FROM approvals") == [("approved", "rev1")]


def test_a2_restart_then_edit_uses_edited_draft(pool, owner):
    s = base(ticket_id="TST-R2")
    start(pool, s)
    pool.close()
    app = create_app(graph_factory=build_stub_graph, token=TOKEN)
    with TestClient(app) as c:
        bad = c.post("/tickets/TST-R2/decision", headers=H,
                     json={"status": "edited", "reviewer": "rev1", "edited_draft": f"send to {iban(9)}"})
        assert bad.status_code == 422
        assert c.post("/tickets/TST-R2/decision", headers=H, json={"status": "edited", "reviewer": "rev1"}).status_code == 422
        assert c.post("/tickets/TST-R2/decision", headers=H,
                      json={"status": "edited", "reviewer": "rev1", "edited_draft": "Final wording."}).status_code == 200
    assert rows(owner, "SELECT masked_body FROM outbox") == [("Final wording.",)]


def test_g_api_auth_and_queue_no_pii(pool, client):
    s = base(priority="P1", ticket_id="TST-Q1", draft="Please verify <IBAN_1> and <EID_1> for the WPS file.")
    start(pool, s)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/queue", headers={"X-Tenant-Id": ENT}).status_code == 401
    assert client.get("/queue", headers={"Authorization": "Bearer nope", "X-Tenant-Id": ENT}).status_code == 401
    assert client.post("/tickets/TST-Q1/decision", json={"status": "approved", "reviewer": "x"}).status_code == 401
    assert client.get("/queue", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 400    # tenant required
    r = client.get("/queue", headers=H)
    assert r.status_code == 200 and "TST-Q1" in r.text and "P1" in r.text
    assert "&lt;IBAN_1&gt;" in r.text
    from solution.a7.pii.patterns import find_regex_spans
    assert find_regex_spans(r.text) == []            # no IBAN/PAN/EID/email/phone-like value in the page


def test_h_cross_tenant_is_404(pool, client, owner):
    start(pool, base(ticket_id="TST-X1"))
    other = {"Authorization": f"Bearer {TOKEN}", "X-Tenant-Id": BUS}
    assert client.get("/tickets/TST-X1", headers=other).status_code == 404
    assert client.post("/tickets/TST-X1/decision", headers=other, json={"status": "approved", "reviewer": "r"}).status_code == 404
    assert "TST-X1" not in client.get("/queue", headers=other).text
    assert rows(owner, "SELECT status FROM approvals WHERE ticket_id='TST-X1'") == [("pending",)]
    assert client.get("/tickets/TST-X1", headers=H).status_code == 200
