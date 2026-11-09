"""Phase 3 Builder A stand-ins for Builder B (tools) and Builder C (human loop). TESTS ONLY; integration removes this file.

graph.py imports the real modules first and falls back to these.
"""
from __future__ import annotations

from typing import Annotated

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import interrupt


@tool
def get_payroll_run_status(month: str, tenant_id: Annotated[str, InjectedState("tenant_id")]) -> str:
    """Stand-in: payroll run status for the current customer in YYYY-MM."""
    return f"tenant {tenant_id} run {month}: state=computing (stub)"


@tool
def lookup_statutory_rule(topic: str, tenant_id: Annotated[str, InjectedState("tenant_id")],
                          as_of: Annotated[str, InjectedState("as_of")]) -> str:
    """Stand-in: statutory value for the customer's region on the ticket date."""
    return f"{topic} as of {as_of} for {tenant_id}: stub"


def get_tools(lane: str) -> list[BaseTool]:
    return [] if lane == "block" else [get_payroll_run_status, lookup_statutory_rule]


def human_approval(state):
    """Stand-in for Builder C: pauses the graph with interrupt(); resume with Command(resume={...})."""
    decision = interrupt({"ticket_id": state.get("ticket_id"), "lane": state.get("lane"), "draft": state.get("draft", "")})
    out = dict(state)
    out["approval"] = {"status": (decision or {}).get("status", "approved"), "reviewer": (decision or {}).get("reviewer", "stub"),
                       "decided_at": None, "edited_draft": (decision or {}).get("edited_draft")}
    return out


def act(state):
    out = dict(state)
    out["actions"] = list(state.get("actions", []))
    return out


def log_and_learn(state):
    out = dict(state)
    out["audit"] = list(state.get("audit", [])) + [{"event": "stub", "lane": state.get("lane")}]
    return out
