"""Tiny stand-in for Builder A's graph: human_approval -> act -> log_and_learn, same interrupt/resume shape.
Used ONLY by Phase 3C tests until integration with solution/a7/graph.py."""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from solution.a7.nodes.act import act
from solution.a7.nodes.human_approval import human_approval
from solution.a7.nodes.log_and_learn import log_and_learn


class StubState(TypedDict, total=False):
    ticket_id: str
    tenant_id: str
    vault_key: str
    lane: str
    priority: str
    category: str
    language: str
    received_at: str
    is_payroll_window: bool
    draft: str
    citations: list
    guard: dict
    route_reasons: list
    tool_calls: list
    approval: dict
    actions: list
    audit: list
    tenant_resolution: dict
    queue: str
    privacy_concern: bool


def build_stub_graph(checkpointer=None, **_: Any):
    g = StateGraph(StubState)
    g.add_node("human_approval", human_approval)
    g.add_node("act", act)
    g.add_node("log_and_learn", log_and_learn)
    g.add_edge(START, "human_approval")
    g.add_edge("human_approval", "act")
    g.add_edge("act", "log_and_learn")
    g.add_edge("log_and_learn", END)
    return g.compile(checkpointer=checkpointer)
