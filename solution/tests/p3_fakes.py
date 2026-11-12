"""Fakes for Phase 3 Builder A tests: deciders, chat model, tenant lookup, retriever, tools."""
from __future__ import annotations

from typing import Annotated, Any

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from solution.a7.decide.base import Decision


def dec(choice: str, conf: float = 0.95, backend: str = "fake") -> Decision:
    return Decision(choice, conf, backend, 0.0, {})


class FakeDecider:
    """decide_multi answers by name; decide() answers the guard's yes/no question."""

    def __init__(self, category="statutory", conf=0.95, urgency="urgent", injection="no", answers="yes", suggest=None):
        self.category, self.conf, self.urgency, self.injection, self.answers = category, conf, urgency, injection, answers
        self.suggest = suggest
        self.name = "fake"
        self.calls = 0

    def decide_multi(self, items):
        self.calls += 1
        return {"category": dec(self.suggest or self.category, self.conf), "urgency": dec(self.urgency, 0.9),
                "is_injection": dec(self.injection, 0.9)}

    def decide(self, question, options, *, context=None):
        return dec(self.answers)


def deciders(auto: FakeDecider | None = None, sug: FakeDecider | None = None) -> dict:
    a = auto or FakeDecider()
    """auto = the primary backend (Nova), sug = the fallback (Qwen3-32B)."""
    return {"bedrock_nova": a, "bedrock_qwen32b": sug or FakeDecider(category=a.suggest or a.category)}


class FakeChat:
    """Scripted chat model. `script` is a list of AIMessage (or callables messages->AIMessage); the last repeats."""

    def __init__(self, script, tools=None):
        self.script, self.tools, self.seen = list(script), tools, []
        self.i = [0]

    def bind_tools(self, tools):
        c = FakeChat(self.script, tools)
        c.i, c.seen = self.i, self.seen
        return c

    def invoke(self, messages, *a, **k):
        self.seen.append((bool(self.tools), len(messages)))
        r = self.script[min(self.i[0], len(self.script) - 1)]
        self.i[0] += 1
        return r(messages) if callable(r) else r


class FakeLLM:
    """llm(tier) factory that records which tiers were requested."""

    def __init__(self, script):
        self.chat, self.tiers = FakeChat(script), []

    def __call__(self, tier):
        self.tiers.append(tier)
        return self.chat


def tool_call(name: str, args: dict, id_="t1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id_, "type": "tool_call"}])


@tool
def whoami(topic: str, tenant_id: Annotated[str, InjectedState("tenant_id")]) -> str:
    """Return who the system says the customer is (tenant_id is injected from state)."""
    return f"tenant={tenant_id} topic={topic} value=42"


def tools_factory(lane: str):
    return [] if lane in ("block", "human_review", "incident") else [whoami]


class FakeLookup:
    CUSTOMERS = {"C-1003": ("Enterprise", "UAE"), "C-1004": ("Enterprise", "UAE"), "C-1050": ("Standard", "UAE"),
                 "C-1031": ("Standard", "IN"), "C-1001": ("Business", "IN")}
    MAP = {"falcon trading llc": ["C-1004"], "falcon contracting llc": ["C-1050"], "dup co": ["C-1001", "C-1003"]}

    def partner(self, name):
        return list(self.MAP.get(" ".join((name or "").split()).casefold(), []))

    def customer(self, cid):
        t = self.CUSTOMERS.get(cid)
        return {"customer_id": cid, "name": cid, "tier": t[0], "region": t[1]} if t else None


RULE = {"source_id": "R-UAE-WPS-2026-07", "source": "rule", "title": "WPS SIF v3", "effective_from": "2026-07-15", "effective_to": None,
        "score": 0.03, "body": "WPS SIF file format moves from v2 to v3 (new mandatory establishment-reference column). "
                               "Effective 2026-07-15. Was: SIF v2 (14 columns). Now: SIF v3 (16 columns, new EDR layout). Status: in_force."}
KB = {"source_id": "KB-0105", "source": "kb", "title": "Downloading payslips in bulk", "effective_from": None, "effective_to": None,
      "score": 0.02, "body": "Downloading payslips in bulk"}
PF = {"source_id": "R-IN-PF-2026-08", "source": "rule", "title": "EPF wage ceiling", "effective_from": "2026-08-01", "effective_to": None,
      "score": 0.03, "body": "EPF statutory wage ceiling raised. Effective 2026-08-01. Was: INR 15,000 per month. Now: INR 25,000 per month."}


def retriever_of(*chunks):
    return lambda state: [dict(c) for c in chunks]


def base_state(**kw) -> dict[str, Any]:
    """What worker.prepare() hands the graph for an email ticket (masked), minus the DB-derived fields."""
    s = dict(ticket_id="PN-T1", vault_key="PN-T1", source="email", channel="email", pii_gate_status="passed",
             record={"ticket_id": "PN-T1", "customer_id": "C-1003"}, masked_text="WPS SIF file error for 36 employees. Did the format change?",
             pii_placeholders={}, received_at="2026-07-29T13:39:33Z", received_tz="+04:00", customer_ref="C-1003",
             customer_ref_how="given", category_raw="Statutory", category="statutory", language="en", is_payroll_window=True, trace=[])
    s.update(kw)
    return s


def fake_dedupe(state):
    """Stand-in for dedupe_cluster (the real one embeds with Bedrock): no link, no incident."""
    return {"parent_ticket_id": None, "incident_id": None, "lane_hint": None, "dedupe": {"skipped": "fake"},
            "trace": list(state.get("trace", [])) + [{"node": "dedupe_cluster", "skipped": "fake"}]}
