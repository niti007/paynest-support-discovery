"""Phase 3+4 integration wiring: dedupe_cluster before route, cache_lookup before the agent, tool cache around status tools."""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from solution.a7 import cache
from solution.a7 import tools as T
from solution.a7.graph import build_graph, thread_config
from solution.a7.tools.client import PayrollClient
from solution.tests.p3_fakes import KB, FakeDecider, FakeLLM, FakeLookup, base_state, deciders, fake_dedupe, retriever_of, tools_factory

pytestmark = pytest.mark.db
HOWTO = "Open Payslips, choose bulk download and follow the steps [KB-0105]."


def run(state, *, dedupe=fake_dedupe, cache_node=None, script=None, auto=None, chunks=(KB,)):
    llm = FakeLLM(script or [AIMessage(content=HOWTO)])
    g = build_graph(MemorySaver(), deciders=deciders(auto or FakeDecider("howto", 0.95, "normal")), llm=llm, tools_factory=tools_factory,
                    tenant_lookup=FakeLookup(), retriever=retriever_of(*chunks), output_decider=FakeDecider(answers="yes"),
                    dedupe_node=dedupe, cache_node=cache_node)
    cfg = thread_config(state["ticket_id"])
    g.invoke(state, cfg)
    return g.get_state(cfg).values, llm


def howto_state(**kw):
    return base_state(masked_text="How do I download all payslips at once?", category="howto", **kw)


def test_lane_hint_incident_maps_to_incident_lane():
    dd = lambda s: {**fake_dedupe(s), "lane_hint": "incident", "incident_id": 7}
    v, _ = run(howto_state(), dedupe=dd)
    assert v["lane"] == "incident" and v["incident_id"] == 7 and any("incident" in r for r in v["route_reasons"])


def test_dedupe_parent_wins_and_none_never_clobbers():
    v, _ = run(howto_state(parent_ticket_id="PN-OLD"))
    assert v["parent_ticket_id"] == "PN-OLD"
    dd = lambda s: {**fake_dedupe(s), "parent_ticket_id": "PN-NEW"}
    v, _ = run(howto_state(parent_ticket_id="PN-OLD"), dedupe=dd)
    assert v["parent_ticket_id"] == "PN-NEW"


@pytest.mark.aws      # the default retriever embeds the query on Bedrock; without this mark the A7_BEDROCK=0 offline CI path fails with no credentials
def test_retrieve_sets_topic_and_region():
    kb = {**KB, "topic": "payslips"}
    v, _ = run(howto_state(), chunks=(kb,))
    assert v["topic"] == "payslips" and v["region"] == "UAE"


def test_cache_hit_skips_agent_but_runs_guard_and_keeps_lane():
    hit = lambda s: {"cache": {"status": "hit", "similarity": 0.97}, "cache_hit": True, "cached_from": "PN-1", "draft": HOWTO,
                     "citations": [{"source_id": "KB-0105", "effective_from": None}]}
    v, llm = run(howto_state(), cache_node=hit)
    assert llm.tiers == [] and v["cache_hit"] is True and v["draft"] == HOWTO
    assert v["lane"] == "auto_answer" and v["guard"]["passed"] and v["approval"]["status"] == "not_required"
    nodes = [t["node"] for t in v["trace"]]
    assert "cache_lookup" in nodes and "agent" not in nodes and "output_guard" in nodes


def test_cache_miss_runs_agent_and_non_cacheable_lane_is_skipped():
    miss = lambda s: {"cache": {"status": "miss"}}
    v, llm = run(howto_state(), cache_node=miss)
    assert llm.tiers == ["cheap"] and v["cache"]["status"] == "miss" and not v.get("cache_hit")
    hit = lambda s: {"cache": {"status": "hit"}, "cache_hit": True, "draft": "x"}
    v, llm = run(howto_state(), cache_node=hit, dedupe=lambda s: {**fake_dedupe(s), "lane_hint": "incident"},
                 script=[AIMessage(content="We are investigating.")])
    assert v["lane"] == "incident" and not v.get("cache_hit") and v["cache"]["status"] == "skipped"


# ---------------------------------------------------------------- tool cache
class FakeValkey:
    def __init__(self):
        self.d, self.ttl = {}, {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v, ex=None):
        self.d[k], self.ttl[k] = v, ex


def test_status_tool_is_cached_30s_and_errors_are_not(monkeypatch):
    import httpx
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        if "payroll-runs" in req.url.path and req.url.path.endswith("/status"):
            return httpx.Response(200, json={"run_id": "R1", "state": "computing"})
        if req.url.path.endswith("/payroll-runs"):
            return httpx.Response(200, json={"items": [{"run_id": "R1"}]})
        return httpx.Response(404, json={"error": "nf"})
    T.set_client(PayrollClient("http://x", "t", transport=httpx.MockTransport(handler)))
    vk = FakeValkey()
    T.set_tool_cache(cache.ToolCache(vk))
    try:
        args = {"tenant_id": "C-1004", "month": "2026-08"}
        a = T.get_payroll_run_status.invoke(args)
        n = len(calls)
        b = T.get_payroll_run_status.invoke(args)
        assert a == b and len(calls) == n and json.loads(b)["ok"] is True
        assert list(vk.ttl.values()) == [30] and all(k.startswith("a7:tool:C-1004:get_payroll_run_status:") for k in vk.d)
        # a different tenant never shares the entry
        T.get_payroll_run_status.invoke({**args, "tenant_id": "C-1003"})
        assert len(calls) > n
        # failures (404) are not stored
        T.get_employee_payslip_status.invoke({"tenant_id": "C-1004", "run_id": "R9", "employee_id": "E1"})
        T.get_employee_payslip_status.invoke({"tenant_id": "C-1004", "run_id": "R9", "employee_id": "E1"})
        assert not any("get_employee_payslip_status" in k for k in vk.d)
    finally:
        T.set_tool_cache(None)
        T.set_client(None)
