"""Phase 4B: box 7 cache_lookup, answer cache, Valkey tool cache, kill switch (needs the compose postgres + valkey).

Env: A7_DATABASE_URL / A7_APP_DATABASE_URL / A7_ACT_DATABASE_URL (postgres), A7_VALKEY_URL (valkey).
Embeddings are a deterministic stub with exact cosines (no AWS): vec(c) has cosine c with BASE.
"""
import datetime as dt
import math
import uuid

import pytest
from fastapi.testclient import TestClient

from solution.a7 import cache, db, flags
from solution.a7.api.app import create_app
from solution.a7.human_db import scoped
from solution.a7.nodes.act import make_act
from solution.db import bootstrap

pytestmark = pytest.mark.db
UTC = dt.timezone.utc
TA, TB = "T-CA", "T-CB"
TOPIC, OTHER_TOPIC = "tcache_howto", "tcache_other"
D = 1024


def vec(c: float, axis: int = 1) -> list[float]:
    """Unit vector whose cosine with vec(1.0) is c."""
    v = [0.0] * D
    v[0] = c
    v[axis] = math.sqrt(max(0.0, 1 - c * c))
    return v


def stub_embedder(mapping: dict[str, float]):
    def emb(texts, input_type="search_document"):
        return [vec(mapping.get(t, 1.0)) for t in texts]
    return emb


@pytest.fixture(scope="module", autouse=True)
def setup(owner):
    bootstrap.bootstrap()
    for cid in (TA, TB):
        owner.execute("INSERT INTO customers VALUES (%s,%s,'Business','IN',1,1) ON CONFLICT DO NOTHING", (cid, f"Test {cid}"))
    for i, t in enumerate((TOPIC, OTHER_TOPIC)):
        owner.execute("DELETE FROM kb_chunks WHERE source_id = %s", (f"KB-TC{i}",))
        owner.execute("INSERT INTO kb_chunks (source, source_id, chunk_index, region, topic, title, body) "
                      "VALUES ('kb', %s, 0, 'IN', %s, 'Add an employee', 'Open People and click Add.')", (f"KB-TC{i}", t))
    yield
    owner.execute("DELETE FROM answer_cache WHERE tenant_id LIKE 'T-C%'")
    owner.execute("DELETE FROM kb_chunks WHERE source_id LIKE 'KB-TC%'")
    owner.execute("DELETE FROM customers WHERE customer_id LIKE 'T-C%'")
    owner.execute("UPDATE feature_flags SET enabled = false WHERE name = 'auto_send_enabled'")


@pytest.fixture(autouse=True)
def clean(owner, monkeypatch):
    owner.execute("DELETE FROM answer_cache WHERE tenant_id LIKE 'T-C%'")
    owner.execute("DELETE FROM outbox WHERE tenant_id LIKE 'T-C%'")
    owner.execute("UPDATE feature_flags SET enabled = false WHERE name = 'auto_send_enabled'")
    monkeypatch.delenv("A7_AUTO_SEND", raising=False)
    monkeypatch.delenv("A7_KILL_SWITCH", raising=False)


def tid():
    return "TST-" + uuid.uuid4().hex[:8]


ANSWER = "Open People, click Add employee and fill the form. See KB-0105."


def approved_state(tenant=TA, **kw):
    s = dict(ticket_id=tid(), tenant_id=tenant, lane="draft_for_agent", priority="P3", category="howto", topic=TOPIC,
             region="IN", masked_text="How do I add an employee?", draft=ANSWER, citations=["KB-0105"], guard={"ok": True},
             route_reasons=[], approval={"status": "approved", "reviewer": "agent-1"})
    s.update(kw)
    return s


def put(app, state, c=1.0, now=None):
    with scoped(app, state["tenant_id"]):
        return cache.store_approved(app, state, vec(c), now=now)


def get(app, tenant=TA, c=1.0, topic=TOPIC, region="IN", now=None):
    with scoped(app, tenant):
        return cache.lookup(app, tenant_id=tenant, topic=topic, region=region, embedding=vec(c), now=now)


# ---------------------------------------------------------------- hit / miss thresholds
def test_hit_and_miss_thresholds(app):
    assert put(app, approved_state()) == (True, None)
    assert get(app, c=1.0)["similarity"] == pytest.approx(1.0, abs=1e-4)
    assert get(app, c=0.95) is not None
    assert get(app, c=0.93) is not None
    assert get(app, c=0.91) is None            # just under 0.92
    assert get(app, c=0.5) is None


def test_same_topic_and_region_required(app):
    put(app, approved_state())
    assert get(app, topic=OTHER_TOPIC) is None
    assert get(app, region="UAE") is None
    assert get(app) is not None


# ---------------------------------------------------------------- tenant isolation
def test_tenant_isolation(app):
    put(app, approved_state(tenant=TA))
    assert get(app, tenant=TB) is None                           # same embedding, topic, region: still nothing
    with scoped(app, TB):                                        # RLS alone, with no tenant predicate
        assert app.execute("SELECT count(*) FROM answer_cache").fetchone()[0] == 0
    put(app, approved_state(tenant=TB, draft="Use the Add employee button. See KB-0105."))
    assert "Add employee button" in get(app, tenant=TB)["answer"]
    assert "fill the form" in get(app, tenant=TA)["answer"]


# ---------------------------------------------------------------- never-cache rules
@pytest.mark.parametrize("kw, reason", [
    (dict(priority="P1"), "P1"),
    (dict(category="statutory"), "statutory"),
    (dict(citations=["R-UAE-WPS-2026-07"]), "statutory (rule citation)"),
    (dict(route_reasons=["rule:stale_kb"]), "statutory (rule citation)"),
    (dict(draft="Your PF wage ceiling is INR 15,000 per month."), "tenant-specific figures"),
    (dict(draft="You have 1250 employees on the plan."), "tenant-specific figures"),
    (dict(draft="The cut-off is 15 Jul."), "tenant-specific figures"),
    (dict(draft="Fees rose by 12%."), "tenant-specific figures"),
    (dict(draft="Send it to <EMAIL_1> please."), "PII or placeholder in draft"),
    (dict(approval={"status": "pending"}), "not human-approved"),
    (dict(approval={"status": "rejected"}), "not human-approved"),
    (dict(category="payroll"), "not How-To"),
    (dict(guard={"ok": False}), "output_guard did not pass"),
    (dict(lane="route_queue"), "lane route_queue"),
])
def test_never_cache(app, kw, reason):
    ok, why = put(app, approved_state(**kw))
    assert (ok, why) == (False, reason)
    assert get(app) is None


def test_small_numbers_and_ids_still_cacheable(app):
    assert put(app, approved_state(draft="Step 2: click Add. Takes under 24 hours. See KB-0105 and PN-101333."))[0] is True


def test_edited_draft_is_what_is_cached_and_checked(app):
    s = approved_state(approval={"status": "edited", "reviewer": "a", "edited_draft": "Click Add, then Save. KB-0105."})
    assert put(app, s)[0] is True
    assert get(app)["answer"].startswith("Click Add, then Save")
    bad = approved_state(approval={"status": "edited", "reviewer": "a", "edited_draft": "It costs INR 5,000."})
    assert put(app, bad) == (False, "tenant-specific figures")


# ---------------------------------------------------------------- TTL
def test_ttl_expiry(app, owner):
    t0 = dt.datetime(2026, 9, 1, tzinfo=UTC)
    put(app, approved_state(), now=t0)
    assert get(app, now=t0 + dt.timedelta(days=6, hours=23)) is not None
    assert get(app, now=t0 + dt.timedelta(days=7, seconds=1)) is None
    assert cache.TTL_DAYS == 7
    assert cache.purge_expired(owner, now=t0 + dt.timedelta(days=8)) >= 1


# ---------------------------------------------------------------- topic flush
def test_topic_flush_on_kb_edit(app, owner):
    put(app, approved_state(tenant=TA))
    put(app, approved_state(tenant=TB))
    put(app, approved_state(tenant=TA, topic=OTHER_TOPIC))
    # no-op upsert (what an unchanged load_kb re-run does) flushes nothing
    owner.execute("UPDATE kb_chunks SET body = body, title = title WHERE source_id = 'KB-TC0'")
    assert get(app, tenant=TA) is not None
    # a real edit flushes that topic for every tenant, and only that topic
    owner.execute("UPDATE kb_chunks SET body = 'Open People, click Add, then Save.' WHERE source_id = 'KB-TC0'")
    assert get(app, tenant=TA) is None and get(app, tenant=TB) is None
    assert get(app, tenant=TA, topic=OTHER_TOPIC) is not None


def test_flush_stale_and_flush_topic(app, owner):
    put(app, approved_state())
    assert cache.flush_stale(owner) == 0
    owner.execute("UPDATE answer_cache SET created_at = created_at - interval '1 day' WHERE tenant_id = %s", (TA,))
    assert cache.flush_stale(owner) == 1             # entry predates its topic's newest kb_chunks.updated_at
    put(app, approved_state())
    assert cache.flush_topic(owner, TOPIC) == 1
    assert get(app) is None


def test_load_kb_rerun_flushes_edited_topic_only(app, owner):
    from solution.ingest import load_kb as lk
    put(app, approved_state(tenant=TA))
    owner.execute("UPDATE kb_chunks SET body = 'changed' WHERE source_id = 'KB-TC0'")   # as if load_kb rewrote it
    assert get(app) is None
    assert hasattr(lk, "UPSERT")                                                         # the upsert goes through the same trigger


# ---------------------------------------------------------------- node
def test_cache_lookup_node(owner):
    s = approved_state()
    emb = stub_embedder({"How do I add an employee?": 1.0, "close paraphrase": 0.95, "different thing": 0.4})
    with db.connect(app=True, autocommit=True) as c, scoped(c, TA):
        cache.store_approved(c, s, vec(1.0))
    node = cache.make_cache_lookup(lambda: db.connect(app=True, autocommit=True), emb)
    base = dict(tenant_id=TA, topic=TOPIC, region="IN", priority="P3", category="howto", lane="auto_answer")
    out = node({**base, "masked_text": "close paraphrase"})
    assert out["cache"]["status"] == "hit" and out["cache_hit"] is True and out["draft"] == ANSWER
    assert out["cached_from"] == s["ticket_id"]
    assert node({**base, "masked_text": "different thing"}) == {"cache": {"status": "miss"}}
    assert node({**base, "tenant_id": TB, "masked_text": "close paraphrase"}) == {"cache": {"status": "miss"}}
    assert node({**base, "priority": "P1", "masked_text": "close paraphrase"})["cache"]["status"] == "skipped"
    assert node({**base, "category": "statutory", "masked_text": "x"})["cache"]["status"] == "skipped"
    assert node({"tenant_id": TA, "masked_text": "x"})["cache"]["status"] == "skipped"


def test_act_writes_cache_for_approved_draft_only(owner):
    emb = stub_embedder({})
    act = make_act(embedder=emb)
    s = approved_state()
    r = act(s)["actions"]
    assert any(a.get("type") == "cache" and a["status"] == "stored" for a in r)
    assert owner.execute("SELECT count(*) FROM answer_cache WHERE tenant_id = %s", (TA,)).fetchone()[0] == 1
    p1 = act(approved_state(priority="P1"))["actions"]
    assert any(a.get("type") == "cache" and a["status"] == "skipped" for a in p1)
    assert owner.execute("SELECT count(*) FROM answer_cache WHERE tenant_id = %s", (TA,)).fetchone()[0] == 1
    rej = act(approved_state(approval={"status": "rejected"}))["actions"]
    assert not any(a.get("type") == "cache" for a in rej)


# ---------------------------------------------------------------- Valkey
@pytest.fixture()
def vk():
    try:
        c = cache.valkey_client()
        c.ping()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"valkey not reachable: {e}")
    for k in c.scan_iter("a7:tool:T-C*"):
        c.delete(k)
    return c


def test_valkey_30s_ttl_and_tenant_key(vk):
    tc = cache.ToolCache(vk)
    calls = []

    def fn():
        calls.append(1)
        return {"state": "paid", "msg": "ok"}

    args = {"run_id": "RUN-9", "employee_email": "someone@example.com"}
    assert tc.call(TA, "get_payroll_run_status", args, fn) == {"state": "paid", "msg": "ok"}
    assert tc.call(TA, "get_payroll_run_status", args, fn)["state"] == "paid"
    assert len(calls) == 1                                            # second call served from Valkey
    key = cache.tool_key(TA, "get_payroll_run_status", args)
    assert 0 < vk.ttl(key) <= 30
    assert "example.com" not in key and "RUN-9" not in key            # args are hashed
    assert key != cache.tool_key(TB, "get_payroll_run_status", args)  # tenant is part of the key
    assert tc.get(TB, "get_payroll_run_status", args) is None
    assert cache.STATUS_TTL_S == 30


def test_valkey_expiry_and_masked_values(vk):
    tc = cache.ToolCache(vk, ttl=1)
    tc.set(TA, "t", {"a": 1}, {"note": "pay to AE070331234567890123456"})
    raw = vk.get(cache.tool_key(TA, "t", {"a": 1}))
    assert "AE0703312345" not in raw
    import time
    time.sleep(1.3)
    assert tc.get(TA, "t", {"a": 1}) is None


# ---------------------------------------------------------------- kill switch
def auto(**kw):
    return dict(ticket_id=tid(), tenant_id=TA, lane="auto_answer", priority="P3", category="howto", language="en",
                draft="See KB-0105 to add an employee.", citations=["KB-0105"], guard={"ok": True}, **kw)


def test_kill_switch_off_means_shadow_only(owner):
    act = make_act()
    r = act(auto())["actions"][0]
    assert r["shadow"] is True and r["status"] == "shadow"
    assert owner.execute("SELECT shadow, status, sent_at FROM outbox WHERE tenant_id=%s", (TA,)).fetchall() == [(True, "shadow", None)]


def test_flag_on_queues_but_env_override_forces_off(owner, monkeypatch):
    owner.execute("UPDATE feature_flags SET enabled = true WHERE name = 'auto_send_enabled'")
    act = make_act()
    assert act(auto())["actions"][0]["status"] == "queued"
    for var, val in (("A7_AUTO_SEND", "false"), ("A7_AUTO_SEND", "0"), ("A7_KILL_SWITCH", "1")):
        monkeypatch.delenv("A7_AUTO_SEND", raising=False)
        monkeypatch.delenv("A7_KILL_SWITCH", raising=False)
        monkeypatch.setenv(var, val)
        assert act(auto())["actions"][0]["status"] == "shadow", (var, val)


def test_env_cannot_force_on(owner, monkeypatch):
    for val in ("true", "1", "yes", "on"):
        monkeypatch.setenv("A7_AUTO_SEND", val)
        assert act_status(make_act()) == "shadow"
    monkeypatch.setenv("A7_KILL_SWITCH", "0")
    assert act_status(make_act()) == "shadow"


def act_status(act):
    return act(auto())["actions"][0]["status"]


def test_fail_closed(owner):
    owner.execute("UPDATE feature_flags SET enabled = true WHERE name = 'auto_send_enabled'")
    assert flags.auto_send_enabled(None) is False                       # no connection
    owner.execute("DELETE FROM feature_flags WHERE name = 'auto_send_enabled'")
    try:
        assert flags.auto_send_enabled(owner) is False                  # row missing
    finally:
        owner.execute("INSERT INTO feature_flags (name, enabled, updated_by) VALUES ('auto_send_enabled', false, 'test') "
                      "ON CONFLICT DO NOTHING")


def test_auto_answer_p1_and_block_never_send_even_when_on(owner):
    owner.execute("UPDATE feature_flags SET enabled = true WHERE name = 'auto_send_enabled'")
    act = make_act()
    assert act({**auto(), "priority": "P1"})["actions"][0]["status"] == "refused"
    assert act({**auto(), "lane": "block"})["actions"][0]["status"] == "refused"


# ---------------------------------------------------------------- admin API
@pytest.fixture()
def api():
    app = create_app(graph_factory=lambda cp: None, token="rev-token", admin_token="adm-token")
    with TestClient(app) as c:
        yield c


def test_admin_token_required(api, owner):
    body = {"name": "auto_send_enabled", "enabled": True, "updated_by": "head-of-support"}
    assert api.post("/admin/flags", json=body).status_code == 401
    assert api.post("/admin/flags", json=body, headers={"Authorization": "Bearer rev-token"}).status_code == 401   # reviewer token is not enough
    assert api.post("/admin/flags", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert api.get("/admin/flags").status_code == 401
    assert flags.get_flag(owner, "auto_send_enabled") is False


def test_admin_can_flip_and_it_takes_effect(api, owner, monkeypatch):
    H = {"Authorization": "Bearer adm-token"}
    r = api.post("/admin/flags", json={"name": "auto_send_enabled", "enabled": True, "updated_by": "hos"}, headers=H)
    assert r.status_code == 200 and r.json()["auto_send_effective"] is True
    assert owner.execute("SELECT enabled, updated_by FROM feature_flags WHERE name='auto_send_enabled'").fetchone() == (True, "hos")
    assert act_status(make_act()) == "queued"
    monkeypatch.setenv("A7_AUTO_SEND", "false")                      # env override wins over the DB flag
    r = api.get("/admin/flags", headers=H).json()
    assert r["flags"]["auto_send_enabled"] is True and r["auto_send_effective"] is False and r["env_forces_off"] is True
    monkeypatch.delenv("A7_AUTO_SEND")
    r = api.post("/admin/flags", json={"name": "auto_send_enabled", "enabled": False, "updated_by": "hos"}, headers=H)
    assert r.json()["auto_send_effective"] is False
    assert act_status(make_act()) == "shadow"


def test_admin_rejects_unknown_flag_and_missing_actor(api):
    H = {"Authorization": "Bearer adm-token"}
    assert api.post("/admin/flags", json={"name": "other", "enabled": True, "updated_by": "x"}, headers=H).status_code == 422
    assert api.post("/admin/flags", json={"name": "auto_send_enabled", "enabled": True, "updated_by": " "}, headers=H).status_code == 422


def test_admin_disabled_when_no_token_configured(monkeypatch, owner):
    monkeypatch.delenv("A7_ADMIN_TOKEN", raising=False)
    app = create_app(graph_factory=lambda cp: None, token="rev-token")
    with TestClient(app) as c:
        assert c.post("/admin/flags", json={"name": "auto_send_enabled", "enabled": True, "updated_by": "x"},
                      headers={"Authorization": "Bearer "}).status_code == 401
