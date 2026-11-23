"""Cache fill (user decision 2026-11-12): reviewer-graded shadow auto_answer drafts and reviewer-approved human_review
How-Tos fill answer_cache under the unchanged never-cache rules. Grading never sends or queues anything.
Needs the compose postgres. Embeddings are a constant stub (identical questions have cosine 1.0); LLMs are scripted."""
import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from solution.a7 import cache, db
from solution.a7.api.app import create_app
from solution.a7.graph import build_graph, thread_config
from solution.a7.nodes.act import make_act
from solution.tests.p3_fakes import KB, FakeDecider, FakeLLM, FakeLookup, base_state, deciders, fake_dedupe, retriever_of, tools_factory
from solution.tests.test_cache import vec

pytestmark = pytest.mark.db
TEN = "C-1003"                       # UAE tenant in the reference data (FakeLookup knows it too)
HOWTO = "Open Payslips, choose bulk download and follow the steps [KB-0105]."
H = {"Authorization": "Bearer rev-token", "X-Tenant-Id": TEN}
KBT = {**KB, "topic": "tcf_payslips"}


def emb(texts, input_type="search_document"):
    return [vec(1.0) for _ in texts]


@pytest.fixture(autouse=True)
def clean(owner):
    def wipe():
        for t in ("answer_cache", "outbox", "shadow_reviews"):
            owner.execute(f"DELETE FROM {t} WHERE tenant_id = %s", (TEN,))
    wipe()
    yield
    wipe()


@pytest.fixture()
def client():
    app = create_app(graph_factory=lambda cp: None, token="rev-token", embedder=emb)
    with TestClient(app) as c:
        yield c


def graph(llm, act_node=None):
    return build_graph(MemorySaver(), deciders=deciders(FakeDecider("howto", 0.95, "normal")), llm=llm, tools_factory=tools_factory,
                       tenant_lookup=FakeLookup(), retriever=retriever_of(KBT), output_decider=FakeDecider(answers="yes"),
                       dedupe_node=fake_dedupe, act_node=act_node or make_act(embedder=emb),
                       cache_node=cache.make_cache_lookup(lambda: db.connect(app=True, autocommit=True), emb))


def run_ticket(llm, tid):
    g = graph(llm)
    cfg = thread_config(tid)
    g.invoke(base_state(ticket_id=tid, vault_key=tid, record={"ticket_id": tid, "customer_id": TEN},
                        masked_text="How do I download all payslips at once?", category="howto"), cfg)
    return g.get_state(cfg).values


def tid():
    return "TCF-" + uuid.uuid4().hex[:8]


def n(owner, table, where="tenant_id = %s", args=(TEN,)):
    return owner.execute(f"SELECT count(*) FROM {table} WHERE {where}", args).fetchone()[0]


def outbox_state(owner):
    return sorted(owner.execute("SELECT status, shadow, sent_at IS NULL FROM outbox WHERE tenant_id = %s", (TEN,)).fetchall())


# ---------------------------------------------------------------- shadow approve -> cache -> hit through the real graph
def test_shadow_approve_fills_cache_and_next_ticket_hits(client, owner):
    t1 = tid()
    llm1 = FakeLLM([AIMessage(content=HOWTO)])
    v1 = run_ticket(llm1, t1)
    assert v1["lane"] == "auto_answer" and v1["approval"]["status"] == "not_required" and not v1.get("cache_hit")
    assert n(owner, "answer_cache") == 0                                  # shadow alone fills nothing
    assert "Shadow auto-answers" in client.get("/queue", headers=H).text and t1 in client.get("/queue", headers=H).text
    before = outbox_state(owner)
    assert before == [("shadow", True, True)]

    r = client.post(f"/shadow/{t1}/review", headers=H, json={"status": "approved", "reviewer": "lead-1"})
    assert r.status_code == 200 and r.json()["cache"] == "stored" and r.json()["sent"] is False
    assert n(owner, "answer_cache") == 1
    assert outbox_state(owner) == before                                  # nothing queued, sent or changed
    sr = owner.execute("SELECT decision, reviewer, cache_status, outbox_id FROM shadow_reviews WHERE ticket_id = %s", (t1,)).fetchone()
    assert sr[:3] == ("approved", "lead-1", "stored") and sr[3] is not None
    assert t1 not in client.get("/queue", headers=H).text                 # graded rows leave the queue

    t2 = tid()
    llm2 = FakeLLM([AIMessage(content="SHOULD NOT BE USED")])
    v2 = run_ticket(llm2, t2)
    assert v2["cache_hit"] is True and v2["cached_from"] == t1 and v2["draft"] == HOWTO and llm2.tiers == []
    assert v2["lane"] == "auto_answer" and v2["cache"]["status"] == "hit"
    assert outbox_state(owner) == [("shadow", True, True)] * 2             # still only shadow rows

    r2 = client.post(f"/shadow/{t2}/review", headers=H, json={"status": "approved", "reviewer": "lead-1"})
    assert r2.json()["cache"] == "skipped" and "cache" in r2.json()["cache_reason"]   # cache-served answer is not re-stored
    assert n(owner, "answer_cache") == 1


def test_shadow_reject_stores_nothing_and_sends_nothing(client, owner):
    t = tid()
    run_ticket(FakeLLM([AIMessage(content=HOWTO)]), t)
    r = client.post(f"/shadow/{t}/review", headers=H, json={"status": "rejected", "reviewer": "lead-1"})
    assert r.status_code == 200 and r.json()["cache"] == "none"
    assert n(owner, "answer_cache") == 0 and outbox_state(owner) == [("shadow", True, True)]
    assert owner.execute("SELECT decision, reviewer FROM shadow_reviews WHERE ticket_id = %s", (t,)).fetchone() == ("rejected", "lead-1")
    v = run_ticket(FakeLLM([AIMessage(content=HOWTO)]), tid())
    assert not v.get("cache_hit")                                         # identical next ticket misses


def test_reviewer_token_required_and_nothing_written(client, owner):
    t = tid()
    run_ticket(FakeLLM([AIMessage(content=HOWTO)]), t)
    body = {"status": "approved", "reviewer": "x"}
    for h in ({"X-Tenant-Id": TEN}, {**H, "Authorization": "Bearer wrong"}, {**H, "Authorization": "Bearer "}):
        assert client.post(f"/shadow/{t}/review", headers=h, json=body).status_code == 401
    assert client.get("/shadow", headers={"X-Tenant-Id": TEN}).status_code == 401
    assert n(owner, "answer_cache") == 0 and n(owner, "shadow_reviews") == 0
    assert client.post(f"/shadow/{t}/review", headers=H, json={"status": "approved", "reviewer": " "}).status_code == 422
    assert client.post(f"/shadow/{t}/review", headers=H, json={"status": "edited", "reviewer": "x"}).status_code == 422
    assert client.post(f"/shadow/{t}/review", headers={**H, "X-Tenant-Id": "C-9999"}, json=body).status_code == 404   # other tenant
    assert client.post(f"/shadow/{t}/review", headers=H, json=body).status_code == 200
    assert client.post(f"/shadow/{t}/review", headers=H, json=body).status_code == 409                               # one grade only


# ---------------------------------------------------------------- never-cache rules still block
def shadow_row(**kw):
    s = dict(ticket_id=tid(), tenant_id=TEN, lane="auto_answer", priority="P3", category="howto", topic="tcf_payslips", region="UAE",
             masked_text="How do I download all payslips at once?", draft=HOWTO, citations=[{"source_id": "KB-0105"}],
             guard={"passed": True}, route_reasons=[], language="en", approval={"status": "not_required"})
    s.update(kw)
    return s


@pytest.mark.parametrize("kw,reason", [
    (dict(category="statutory"), "statutory"),
    (dict(citations=[{"source_id": "R-UAE-WPS-2026-07"}]), "statutory"),
    (dict(route_reasons=["rule5:statutory->draft_for_agent(P2)"]), "statutory"),
    (dict(draft="Your bonus of AED 4,500 is paid on 15 Jul 2026 [KB-0105]."), "figures"),
    (dict(draft="Contact <EMAIL_1> for the steps [KB-0105]."), "PII"),
    (dict(masked_text="My IBAN is AE070331234567890123456, how do I add a bank?"), "PII in question"),
    (dict(category="payroll"), "not How-To"),
    (dict(is_injection=True), "injection"),
    (dict(topic=None), "missing"),
])
def test_never_cache_rules_still_block_on_shadow_approve(client, owner, kw, reason):
    s = shadow_row(**kw)
    make_act()(s)
    assert n(owner, "outbox") == 1
    r = client.post(f"/shadow/{s['ticket_id']}/review", headers=H, json={"status": "approved", "reviewer": "lead-1"})
    assert r.status_code == 200 and r.json()["cache"] == "skipped" and reason.lower() in r.json()["cache_reason"].lower(), r.json()
    assert n(owner, "answer_cache") == 0 and outbox_state(owner) == [("shadow", True, True)]
    assert owner.execute("SELECT decision, cache_status FROM shadow_reviews WHERE ticket_id = %s", (s["ticket_id"],)).fetchone() == ("approved", "skipped")


def test_p1_and_failed_guard_never_even_get_a_shadow_row(owner):
    act = make_act()
    assert act(shadow_row(priority="P1"))["actions"][0]["status"] == "refused"
    assert act(shadow_row(guard={"passed": False}))["actions"][0]["status"] == "refused"
    assert n(owner, "outbox") == 0
    assert cache.never_cache_reason(priority="P1", category="howto", lane="auto_answer", citations=[], route_reasons=[], draft=HOWTO,
                                    approval_status="approved") == "P1"


def test_shadow_grading_does_not_flip_when_kill_switch_on(client, owner):
    """Even with auto-send enabled the grade endpoint touches nothing in the outbox (the row was queued by act, not graded)."""
    s = shadow_row()
    make_act()(s)
    r = client.post(f"/shadow/{s['ticket_id']}/review", headers=H, json={"status": "approved", "reviewer": "lead-1"})
    assert r.status_code == 200 and outbox_state(owner) == [("shadow", True, True)]
    assert owner.execute("SELECT enabled FROM feature_flags WHERE name = 'auto_send_enabled'").fetchone()[0] is False


# ---------------------------------------------------------------- human_review How-Tos
def hr(**kw):
    s = shadow_row(lane="human_review", route_reasons=["rule4:classify_human->human_review"],
                   approval={"status": "approved", "reviewer": "lead-2"})
    s.update(kw)
    return s


def test_human_review_approved_howto_is_cached_and_nothing_is_queued(owner):
    act = make_act(embedder=emb)
    s = hr()
    res = act(s)["actions"]
    assert [a for a in res if a["type"] == "cache"][0]["status"] == "stored"
    assert n(owner, "answer_cache") == 1 and n(owner, "outbox") == 0
    assert owner.execute("SELECT approved_by FROM answer_cache WHERE tenant_id = %s", (TEN,)).fetchone()[0] == "lead-2"


@pytest.mark.parametrize("kw", [dict(approval={"status": "rejected", "reviewer": "r"}), dict(category="statutory"), dict(priority="P1"),
                                dict(draft="Your refund of INR 12,000 is due [KB-0105]."), dict(citations=[{"source_id": "R-IN-PF-2026-08"}]),
                                dict(cache_hit=True)])
def test_human_review_never_cache_rules(owner, kw):
    make_act(embedder=emb)(hr(**kw))
    assert n(owner, "answer_cache") == 0 and n(owner, "outbox") == 0


def test_edited_human_review_draft_is_what_is_cached(owner):
    make_act(embedder=emb)(hr(approval={"status": "edited", "reviewer": "lead-2", "edited_draft": "Use bulk download in Payslips [KB-0105]."}))
    assert owner.execute("SELECT answer FROM answer_cache WHERE tenant_id = %s", (TEN,)).fetchone()[0].startswith("Use bulk download")
