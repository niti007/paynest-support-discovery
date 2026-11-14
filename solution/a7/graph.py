"""The A7 graph: resolve_tenant -> classify -> dedupe_cluster -> route -> (block -> log) | retrieve -> cache_lookup -> (agent) -> output_guard
-> human_approval -> act -> log_and_learn.

- pii_gate + normalise run OUTSIDE the graph (worker.py). The graph input is the masked record plus vault refs only.
- thread_id = ticket id. Checkpointer: PostgresSaver on a connection with autocommit=True and dict_row;
  LANGGRAPH_STRICT_MSGPACK=true (set before langgraph is imported).
- build_graph() takes every collaborator as an argument so tests and Builder C can inject fakes.
"""
from __future__ import annotations

import os

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from contextlib import contextmanager  # noqa: E402
from typing import Any, Callable  # noqa: E402

from langgraph.graph import END, START, StateGraph  # noqa: E402

from . import db  # noqa: E402
from .nodes.agent import make_agent  # noqa: E402
from .nodes.classify import make_classify  # noqa: E402
from .nodes.dedupe_cluster import make_dedupe_cluster  # noqa: E402
from .nodes.output_guard import make_output_guard  # noqa: E402
from .nodes.resolve_tenant import make_resolve_tenant  # noqa: E402
from .nodes.retrieve import make_retrieve  # noqa: E402
from .nodes.route import route  # noqa: E402
from .state import TicketState  # noqa: E402

from .nodes.act import act  # noqa: E402
from .nodes.human_approval import human_approval  # noqa: E402
from .nodes.log_and_learn import log_and_learn  # noqa: E402
from .tools import get_tools  # noqa: E402


def delta(fn: Callable, keep_tool_calls: bool = False) -> Callable:
    """Nodes return full state copies; LangGraph reducers (tool_calls: operator.add) need DELTAS, so return only changed keys."""
    def wrapped(state: TicketState) -> dict:
        res = fn(state)
        return {k: v for k, v in res.items() if (k == "tool_calls" and keep_tool_calls) or
                (k != "tool_calls" and (k not in state or state[k] != v))}
    wrapped.__name__ = getattr(fn, "__name__", "node")
    return wrapped


def dedupe_delta(fn: Callable) -> Callable:
    """dedupe_cluster writes parent_ticket_id/incident_id/lane_hint/dedupe/trace. Its non-null parent wins; a None result never
    clobbers a non-null value that resolve_tenant (or the input) already put in state."""
    def wrapped(state: TicketState) -> dict:
        res = dict(fn(state))
        if res.get("parent_ticket_id") is None and state.get("parent_ticket_id"):
            res.pop("parent_ticket_id", None)
        return {k: v for k, v in res.items() if k not in state or state[k] != v}
    wrapped.__name__ = "dedupe_cluster"
    return wrapped


CACHEABLE_LANES = ("auto_answer", "draft_for_agent")


def cache_delta(fn: Callable) -> Callable:
    """cache_lookup only serves lanes that may carry a cached How-To answer; it never changes lane."""
    def wrapped(state: TicketState) -> dict:
        if state.get("lane") not in CACHEABLE_LANES:
            res = {"cache": {"status": "skipped", "reason": f"lane {state.get('lane')}"}}
        else:
            res = dict(fn(state))
        res["trace"] = list(state.get("trace", [])) + [{"node": "cache_lookup", **{k: v for k, v in res.get("cache", {}).items()
                                                                               if k in ("status", "similarity", "reason")}}]
        return {k: v for k, v in res.items() if k not in state or state[k] != v}
    wrapped.__name__ = "cache_lookup"
    return wrapped


def after_cache(s: TicketState) -> str:
    return "output_guard" if s.get("cache_hit") else "agent"


def after_route(s: TicketState) -> str:
    if s.get("lane") == "block":        # no draft, no tools; human_approval records "not_required", log_and_learn audits it
        return "human_approval"
    if not s.get("tenant_id"):          # ambiguous tenant: nothing tenant-scoped can run; a person decides
        return "hold_for_tenant"
    return "retrieve"


def hold_for_tenant(state: TicketState) -> dict:
    """Ambiguous tenant: every C node is tenant-scoped (RLS, approvals.tenant_id), so none can run. The graph ends here with an
    explicit status; a person resolves the customer (out-of-band) and the ticket is re-submitted."""
    return {"approval": {"status": "pending_tenant_resolution", "reviewer": None, "decided_at": None, "edited_draft": None}}


def after_guard(s: TicketState) -> str:
    g = s.get("guard") or {}
    return "human_approval" if g.get("passed") or g.get("final") else "agent"


def build_graph(checkpointer: Any = None, *, deciders: dict | None = None, llm: Callable[[str], Any] | None = None,
                tools_factory: Callable[[str], list] | None = None, tenant_lookup: Any = None, retriever: Any = None,
                output_decider: Any = None, human_approval_node: Callable | None = None, act_node: Callable | None = None,
                log_node: Callable | None = None, dedupe_node: Callable | None = None, cache_node: Callable | None = None,
                tool_cache: Any = None, stale_checker: Any = None):
    if llm is None:
        from .llm import llm as default_llm
        llm = default_llm
    if tool_cache is None and tools_factory is None and os.environ.get("A7_TOOL_CACHE", "1") != "0":
        from .cache import ToolCache
        tool_cache = ToolCache()            # lazy Valkey client; any Valkey error falls through to the live call
    if tool_cache is not None and tool_cache is not False:
        from .tools import set_tool_cache
        set_tool_cache(tool_cache)
    if dedupe_node is None:
        dedupe_node = make_dedupe_cluster()
    if cache_node is None:
        from .cache import make_cache_lookup
        cache_node = make_cache_lookup(lambda: db.connect(app=True, autocommit=True))
    agent_fn = make_agent(llm, tools_factory or get_tools)

    def agent_node(state: TicketState) -> dict:
        res = agent_fn(state)
        res["cache_hit"] = False         # a guard-failed cached draft is rewritten by the agent: no longer a cache answer
        return res

    g = StateGraph(TicketState)
    g.add_node("resolve_tenant", delta(make_resolve_tenant(tenant_lookup)))
    g.add_node("classify", delta(make_classify(deciders)))
    g.add_node("dedupe_cluster", dedupe_delta(dedupe_node))
    g.add_node("route", delta(route))
    g.add_node("retrieve", delta(make_retrieve(retriever)))
    g.add_node("cache_lookup", cache_delta(cache_node))
    g.add_node("agent", delta(agent_node, keep_tool_calls=True))
    g.add_node("output_guard", delta(make_output_guard(output_decider, stale_checker=stale_checker)))
    g.add_node("human_approval", delta(human_approval_node or human_approval))
    g.add_node("act", delta(act_node or act))
    g.add_node("log_and_learn", delta(log_node or log_and_learn))
    g.add_node("hold_for_tenant", hold_for_tenant)
    g.add_edge("hold_for_tenant", END)
    g.add_edge(START, "resolve_tenant")
    g.add_edge("resolve_tenant", "classify")
    g.add_edge("classify", "dedupe_cluster")
    g.add_edge("dedupe_cluster", "route")
    g.add_conditional_edges("route", after_route, ["hold_for_tenant", "human_approval", "retrieve"])
    g.add_edge("retrieve", "cache_lookup")
    g.add_conditional_edges("cache_lookup", after_cache, ["agent", "output_guard"])
    g.add_edge("agent", "output_guard")
    g.add_conditional_edges("output_guard", after_guard, ["human_approval", "agent"])
    g.add_edge("human_approval", "act")
    g.add_edge("act", "log_and_learn")
    g.add_edge("log_and_learn", END)
    return g.compile(checkpointer=checkpointer)


def thread_config(ticket_id: str) -> dict:
    return {"configurable": {"thread_id": ticket_id}}


@contextmanager
def postgres_checkpointer(dsn: str | None = None):
    """PostgresSaver on a connection with autocommit=True and dict_row (both are required by the saver)."""
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row

    with psycopg.connect(dsn or db.app_dsn(), autocommit=True, row_factory=dict_row, prepare_threshold=0) as conn:
        yield PostgresSaver(conn)
