"""Gate 3 validator fixes: act/admin DSN derivation (F2), L1->L2 timer on every non-block lane (F3), privacy flag + timer (F4),
kill-switch least privilege (F5). Needs the compose postgres for the DB tests."""
from __future__ import annotations

import datetime as dt
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from solution.a7 import db, escalation, human_db
from solution.a7.api.app import create_app
from solution.a7.graph import build_graph
from solution.a7.nodes.classify import make_classify, privacy_fallback
from solution.db import bootstrap
from solution.tests.p3_fakes import KB, RULE, FakeDecider, FakeLLM, base_state, deciders, fake_dedupe, retriever_of, tools_factory

UTC = dt.timezone.utc
H1 = dt.timedelta(hours=1)


# ---- F2: DSNs follow the app DSN -------------------------------------------------------------------------------
def test_act_and_admin_dsn_follow_app_dsn(monkeypatch):
    for v in ("A7_ACT_DATABASE_URL", "A7_ADMIN_DATABASE_URL", "A7_ACT_PASSWORD", "A7_ADMIN_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("A7_APP_DATABASE_URL", "postgresql://app_user:x@dbhost:55542/a7")
    assert human_db.act_dsn() == "postgresql://a7_act:a7_act_dev@dbhost:55542/a7"
    assert human_db.admin_dsn() == "postgresql://a7_admin:a7_admin_dev@dbhost:55542/a7"
    monkeypatch.setenv("A7_ACT_PASSWORD", "p@ss/w:rd")
    assert human_db.act_dsn() == "postgresql://a7_act:p%40ss%2Fw%3Ard@dbhost:55542/a7"
    monkeypatch.setenv("A7_ACT_DATABASE_URL", "postgresql://u:p@elsewhere:1/a7")          # explicit setting still wins
    assert human_db.act_dsn() == "postgresql://u:p@elsewhere:1/a7"
    monkeypatch.delenv("A7_APP_DATABASE_URL")
    monkeypatch.delenv("A7_ACT_DATABASE_URL")
    monkeypatch.delenv("A7_ACT_PASSWORD")
    assert human_db.act_dsn() == "postgresql://a7_act:a7_act_dev@localhost:55432/a7"      # the default project is unchanged


# ---- F3: pure timers -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("lane", ["route_queue", "auto_answer", "draft_for_agent", "human_review", "incident"])
def test_l1_to_l2_timer_for_every_lane_but_block(lane):
    now = dt.datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    for prio, h in (("P1", 1), ("P2", 4), ("P3", 4)):
        t = {x["kind"]: x for x in escalation.timers_for({"lane": lane, "priority": prio, "received_at": "2026-09-10T08:00:00Z"}, "Standard", now)}
        assert t["l1_to_l2"]["due_at"] == now + h * H1, (lane, prio)


def test_no_l1_to_l2_timer_for_block():
    assert escalation.timers_for({"lane": "block", "priority": "P1", "received_at": "2026-09-10T08:00:00Z"}, "Standard") == []


# ---- F4: classifier flag + fallback ----------------------------------------------------------------------------
def classify(state, privacy):
    d = FakeDecider("payroll", 0.9, "normal", privacy=privacy)
    return make_classify(deciders(d))(base_state(**state))


def test_classify_sets_privacy_concern_from_model_flag():
    s = classify({}, "yes")
    assert s["privacy_concern"] is True and "privacy_concern:model" in s["route_reasons"]
    assert classify({}, "no")["privacy_concern"] is False


def test_classify_tolerates_backend_without_privacy_answer():
    class NoPrivacy(FakeDecider):
        def decide_multi(self, items):
            r = super().decide_multi(items)
            r.pop("privacy_concern")
            return r
    s = make_classify(deciders(NoPrivacy("payroll", 0.9, "normal")))(base_state())
    assert s["privacy_concern"] is False


def test_privacy_code_fallback():
    assert privacy_fallback({"pii_gate_status": "blocked"}) == "pii_gate_blocked"
    assert privacy_fallback({"tenant_id": "C-1004", "masked_text": "payslip of C-1031 was sent to us"}) == "other_tenant_id_in_text"
    assert privacy_fallback({"tenant_id": "C-1004", "masked_text": "our C-1004 account, payslip missing"}) is None
    assert privacy_fallback({"tenant_id": "C-1004", "customer_ref": "C-1004", "masked_text": "no ids here"}) is None
    assert classify({"tenant_id": "C-1004", "masked_text": "we received the payslip of C-1031"}, "no")["privacy_concern"] is True


# ---- graph + DB ------------------------------------------------------------------------------------------------
pytestmark_db = pytest.mark.db
ENT = "T-FX"
GOOD = ("From 15 Jul 2026 the SIF file uses v3 with 16 columns and a mandatory establishment-reference column "
        "[R-UAE-WPS-2026-07, effective 15 Jul 2026].")


@pytest.fixture()
def fxdb(owner):
    bootstrap.bootstrap()
    owner.execute("INSERT INTO customers VALUES (%s,%s,'Enterprise','IN',1,1) ON CONFLICT DO NOTHING", (ENT, "Test fx"))
    yield owner
    owner.execute("TRUNCATE approvals, escalations, outbox, audit_log, incidents, kb_suggestions, partner_map_suggestions")
    owner.execute("DELETE FROM customers WHERE customer_id = %s", (ENT,))


@pytest.fixture()
def pool():
    p = ConnectionPool(db.app_dsn(), min_size=1, max_size=4, open=True,
                       kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0})
    yield p
    p.close()


class Lookup:
    def partner(self, name):
        return []

    def customer(self, cid):
        return {"customer_id": cid, "name": cid, "tier": "Enterprise", "region": "IN"} if cid == ENT else None


def run_graph(pool, category, privacy):
    tid = "FX-" + uuid.uuid4().hex[:8]
    g = build_graph(PostgresSaver(pool), deciders=deciders(FakeDecider(category, 0.95, "normal", privacy=privacy)),
                    llm=FakeLLM([AIMessage(content=GOOD)]), tools_factory=tools_factory, tenant_lookup=Lookup(),
                    retriever=retriever_of(RULE, KB), output_decider=FakeDecider(answers="yes"), dedupe_node=fake_dedupe)
    s = base_state(ticket_id=tid, vault_key=tid, customer_ref=ENT, record={"ticket_id": tid, "customer_id": ENT},
                   masked_text=f"TKT:{tid} payroll run question")
    g.invoke(s, {"configurable": {"thread_id": tid}}, durability="sync")
    return tid, g.get_state({"configurable": {"thread_id": tid}}).values


def timers(owner, tid):
    return {k: d for k, d in owner.execute("SELECT kind, due_at FROM escalations WHERE ticket_id = %s", (tid,)).fetchall()}


def test_route_queue_ticket_gets_l1_to_l2_timer(fxdb, pool):
    tid, v = run_graph(pool, "payroll", "no")
    assert v["lane"] == "route_queue" and v["approval"]["status"] == "not_required"
    t = timers(fxdb, tid)
    assert t == {"l1_to_l2": dt.datetime(2026, 7, 29, 13, 39, 33, tzinfo=UTC) + 4 * H1}      # received_at of base_state, P2/P3 -> 4 h


def test_privacy_ticket_arms_dpo_security_timer_in_one_hour_and_normal_ticket_does_not(fxdb, pool):
    tid, v = run_graph(pool, "payroll", "yes")
    assert v["privacy_concern"] is True
    t = timers(fxdb, tid)
    assert t["privacy_dpo_security"] == dt.datetime(2026, 7, 29, 13, 39, 33, tzinfo=UTC) + H1
    assert fxdb.execute("SELECT level FROM escalations WHERE ticket_id = %s AND kind = 'privacy_dpo_security'", (tid,)).fetchone()[0] == "DPO+Security"
    # it fires (DPO + Security notified) once due, even though no human decided
    fired = escalation.run_due(dt.datetime(2026, 7, 29, 14, 40, tzinfo=UTC))
    assert fired and fxdb.execute("SELECT count(*) FROM outbox WHERE ticket_id = %s AND kind = 'notify' AND channel = 'DPO+Security'", (tid,)).fetchone()[0] == 1
    tid2, v2 = run_graph(pool, "payroll", "no")
    assert v2["privacy_concern"] is False and "privacy_dpo_security" not in timers(fxdb, tid2)


# ---- F5: kill switch least privilege ---------------------------------------------------------------------------
def test_app_user_cannot_write_feature_flags_but_can_read(fxdb):
    with db.connect(app=True, autocommit=True) as app:
        assert app.execute("SELECT enabled FROM feature_flags WHERE name = 'auto_send_enabled'").fetchone() is not None
        for q in ("UPDATE feature_flags SET enabled = true WHERE name = 'auto_send_enabled'",
                  "INSERT INTO feature_flags (name, enabled) VALUES ('x', true)",
                  "DELETE FROM feature_flags"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(q)
    assert fxdb.execute("SELECT enabled FROM feature_flags WHERE name = 'auto_send_enabled'").fetchone()[0] is False


def test_admin_role_writes_flag_and_nothing_else(fxdb):
    with psycopg.connect(human_db.admin_dsn(), autocommit=True) as a:
        a.execute("UPDATE feature_flags SET enabled = true, updated_by = 'fx' WHERE name = 'auto_send_enabled'")
        assert a.execute("SELECT enabled FROM feature_flags WHERE name = 'auto_send_enabled'").fetchone()[0] is True
        a.execute("UPDATE feature_flags SET enabled = false, updated_by = 'fx' WHERE name = 'auto_send_enabled'")
        for q in ("SELECT * FROM pii_vault", "SELECT * FROM outbox", "SELECT * FROM audit_log"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                a.execute(q)


def test_admin_endpoint_flips_flag_through_a7_admin_and_worker_pool_cannot(fxdb):
    app = create_app(graph_factory=lambda cp: None, token="rev", admin_token="adm")
    with TestClient(app) as c:
        H = {"Authorization": "Bearer adm"}
        r = c.post("/admin/flags", json={"name": "auto_send_enabled", "enabled": True, "updated_by": "hos"}, headers=H)
        assert r.status_code == 200 and r.json()["enabled"] is True
        assert fxdb.execute("SELECT enabled, updated_by FROM feature_flags WHERE name = 'auto_send_enabled'").fetchone() == (True, "hos")
        assert c.get("/admin/flags", headers=H).json()["flags"]["auto_send_enabled"] is True
        # the worker's own pool (app_user) is read-only on the flag
        with app.state.pool.connection() as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE feature_flags SET enabled = false WHERE name = 'auto_send_enabled'")
        r = c.post("/admin/flags", json={"name": "auto_send_enabled", "enabled": False, "updated_by": "hos"}, headers=H)
        assert r.status_code == 200
    assert fxdb.execute("SELECT enabled FROM feature_flags WHERE name = 'auto_send_enabled'").fetchone()[0] is False


# ---- G2: route_reasons reports the effective auto-send state (the one act uses) ----------------------------------
def _reason(state=None):
    from solution.a7.nodes.route import decide_lane
    s = base_state(category="howto", priority="P3", tenant_id="C-1004", classify_lane="auto")
    return [r for r in decide_lane(s)[2] if "auto_send_effective" in r]


def test_route_reason_reports_effective_auto_send(fxdb, monkeypatch):
    monkeypatch.delenv("A7_AUTO_SEND", raising=False)
    monkeypatch.delenv("A7_KILL_SWITCH", raising=False)
    assert _reason() == ["rule7:howto_P3->auto_answer(auto_send_effective=false)"]
    fxdb.execute("UPDATE feature_flags SET enabled = true WHERE name = 'auto_send_enabled'")
    try:
        assert _reason() == ["rule7:howto_P3->auto_answer(auto_send_effective=true)"]
        monkeypatch.setenv("A7_AUTO_SEND", "false")                    # OFF-only env override, same as act
        assert _reason() == ["rule7:howto_P3->auto_answer(auto_send_effective=false)"]
        monkeypatch.setenv("A7_AUTO_SEND", "true")                     # env can never force ON, and it is not what reads "true"
        monkeypatch.setenv("A7_KILL_SWITCH", "1")
        assert _reason() == ["rule7:howto_P3->auto_answer(auto_send_effective=false)"]
    finally:
        fxdb.execute("UPDATE feature_flags SET enabled = false WHERE name = 'auto_send_enabled'")
