"""Read-only, tenant-locked tools for the agent loop (box 9).

Isolation: `tenant_id` (and `received_at`) are `InjectedState` parameters. They are not in the schema
the model sees, and ToolNode fills them from graph state, so the model can neither set nor override
them. Every tool is GET-only against the payroll API; there are no write tools.

Usage (Builder A):
    from solution.a7.tools import get_tools, records_from_messages
    tools = get_tools(state["lane"]);  llm.bind_tools(tools);  ToolNode(tools)
    # after the ToolNode ran, in a wrapper node:
    return {"tool_calls": records_from_messages(new_messages)}   # [{name, args_masked, ok}]

Tool outputs are JSON strings, scanned with pii/scanner.py; detected PII is replaced by [TYPE].
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Annotated, Any, Callable

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from solution.a7 import db
from solution.a7.search import hybrid_search

from .client import ApiError, PayrollClient, get_client, set_client  # noqa: F401
from .masking import mask_obj

TenantId = Annotated[str, InjectedState("tenant_id")]
ReceivedAt = Annotated[str, InjectedState("received_at")]


def _out(payload: dict) -> str:
    return json.dumps(mask_obj(payload), ensure_ascii=False)


# ---- 30 s status/profile cache (box 7, Valkey). Off unless a ToolCache is installed (graph.build_graph does it). ----------
_tool_cache: Any = None


def set_tool_cache(tc: Any) -> None:
    global _tool_cache
    _tool_cache = tc


class _Uncached(Exception):
    """Carries a failed tool output out of ToolCache.call so errors (429, 404) are never cached."""

    def __init__(self, out: str):
        self.out = out


def _cached(tenant_id: str, tool_name: str, args: dict, fn: Callable[[], str]) -> str:
    """Read-only status/profile tools only. Any Valkey problem falls through to the live call. Only ok:true is stored."""
    tc = _tool_cache
    if tc is None:
        return fn()

    def live() -> str:
        out = fn()
        try:
            ok = json.loads(out).get("ok", True) is not False
        except (ValueError, TypeError, AttributeError):
            ok = False
        if not ok:
            raise _Uncached(out)
        return out
    try:
        return tc.call(tenant_id, tool_name, args, live)
    except _Uncached as u:
        return u.out
    except Exception:  # noqa: BLE001 - cache down: serve live
        return fn()


def _call(fn: Callable[[], dict]) -> str:
    try:
        return _out({"ok": True, **fn()})
    except ApiError as e:
        # 403/404 are deliberately indistinguishable to the model: no existence hints across tenants.
        err = "not_found_or_forbidden" if e.status in (403, 404) else e.error
        extra = {"retry_after": e.retry_after} if e.retry_after else {}
        return _out({"ok": False, "error": err, **extra})
    except (KeyError, ValueError) as e:
        return _out({"ok": False, "error": f"bad_request: {e}"})


def _as_of(received_at: str | None) -> str:
    if not received_at:
        raise ValueError("received_at missing from state")
    return date.fromisoformat(received_at[:10]).isoformat()


@tool
def get_tenant_profile(tenant_id: TenantId) -> str:
    """Get the current customer's profile: company name, region (IN or UAE), contract tier, employee count."""
    return _cached(tenant_id, "get_tenant_profile", {}, lambda: _call(lambda: get_client().get(f"/tenants/{tenant_id}", tenant_id)))


@tool
def list_payroll_runs(tenant_id: TenantId, month: str | None = None) -> str:
    """List the current customer's payroll runs, newest first. Optional month filter as YYYY-MM."""
    return _cached(tenant_id, "list_payroll_runs", {"month": month}, lambda: _call(lambda: get_client().get(
        f"/tenants/{tenant_id}/payroll-runs", tenant_id, {"month": month} if month else None)))


@tool
def get_payroll_run_status(tenant_id: TenantId, run_id: str | None = None, month: str | None = None) -> str:
    """Get the state of a payroll run (draft, computing, awaiting_approval, approved, bank_file_generated,
    paid, failed) and the last engine message. Give run_id, or month (YYYY-MM), or neither for the latest run."""
    def go():
        c, rid = get_client(), run_id
        if not rid:
            runs = c.get(f"/tenants/{tenant_id}/payroll-runs", tenant_id, {"month": month} if month else None)
            if not runs["items"]:
                return {"found": False}
            rid = runs["items"][0]["run_id"]
        return {"found": True, **c.get(f"/payroll-runs/{rid}/status", tenant_id)}
    return _cached(tenant_id, "get_payroll_run_status", {"run_id": run_id, "month": month}, lambda: _call(go))


@tool
def get_employee_payslip_status(tenant_id: TenantId, run_id: str, employee_id: str) -> str:
    """Check whether an employee's payslip exists for a run (generated or pending). Returns status only:
    never names, amounts or bank details."""
    return _cached(tenant_id, "get_employee_payslip_status", {"run_id": run_id, "employee_id": employee_id},
                   lambda: _call(lambda: get_client().get(f"/payroll-runs/{run_id}/employees/{employee_id}/payslip", tenant_id)))


@tool
def get_statutory_rule(tenant_id: TenantId, received_at: ReceivedAt, rule_area: str, region: str) -> str:
    """Look up the statutory rule in force on the ticket's date, for a rule area such as 'wps', 'pf', 'tds',
    'gratuity'. region is IN or UAE. Quote the rule_id and effective date in any answer."""
    def go():
        as_of = _as_of(received_at)   # always the ticket date, never "today"
        data = get_client().get("/statutory/rates", tenant_id, {"region": region, "as_of": as_of})
        words = [w for w in rule_area.lower().replace("_", " ").split() if len(w) > 1]
        hit = [r for r in data["rules"] if any(w in (r["topic"] + " " + r["title"]).lower().replace("_", " ")
                                               for w in words)]
        return {"region": data["region"], "as_of": data["as_of"], "rules": hit,
                "available_topics": [r["topic"] for r in data["rules"]] if not hit else None}
    return _call(go)


def _search(tenant_id: str, query: str, as_of: str) -> dict:
    embed_q = None
    if os.environ.get("A7_KB_EMBED") == "1":      # Bedrock embedding of the query (off by default)
        from solution.a7.embed import embed
        embed_q = embed([query], "search_query")[0]
    with db.connect(app=True, autocommit=True) as conn, db.tenant(conn, tenant_id):
        row = conn.execute("SELECT region FROM customers WHERE customer_id = %s", (tenant_id,)).fetchone()
        if not row:
            raise ApiError(403, "forbidden")
        rows = hybrid_search(conn, query, region=row[0], as_of=date.fromisoformat(as_of),
                             query_embedding=embed_q, k=5)
        results = []
        for r in rows:
            parts = conn.execute(
                "SELECT title, body FROM kb_chunks WHERE source = %s AND source_id = %s ORDER BY chunk_index",
                (r["source"], r["source_id"])).fetchall()
            results.append({"source": r["source"], "source_id": r["source_id"], "topic": r["topic"],
                            "region": r["region"], "effective_from": str(r["effective_from"] or ""),
                            "title": parts[0][0] if parts else "",
                            "text": " ".join(p[1] for p in parts)[:1500]})
    return {"as_of": as_of, "results": results}


@tool
def search_kb(tenant_id: TenantId, received_at: ReceivedAt, query: str) -> str:
    """Search the knowledge base and statutory rules for passages relevant to the query. Results are limited to
    the customer's region and to what was valid on the ticket's date; each has a source_id to cite."""
    return _call(lambda: _search(tenant_id, query, _as_of(received_at)))


STATUS_TOOLS = [get_payroll_run_status, list_payroll_runs, get_employee_payslip_status]
ALL_TOOLS = STATUS_TOOLS + [get_tenant_profile, get_statutory_rule, search_kb]


def get_tools(lane: str | None) -> list:
    """block/human_review/incident/unknown -> []; route_queue -> status tools; draft_for_agent/auto_answer -> all."""
    if lane in ("draft_for_agent", "auto_answer"):
        return list(ALL_TOOLS)
    if lane == "route_queue":
        return list(STATUS_TOOLS)
    return []


# ---- recording tool_calls ------------------------------------------------------------------------------------
def tool_call_record(name: str, args: dict, ok: bool) -> dict:
    return {"name": name, "args_masked": mask_obj(args), "ok": ok}


def _ok(msg: ToolMessage) -> bool:
    if getattr(msg, "status", "success") == "error":
        return False
    try:
        return json.loads(msg.content).get("ok", True) is not False
    except (ValueError, TypeError, AttributeError):
        return True


def records_from_messages(messages: list[Any]) -> list[dict]:
    """Build [{name, args_masked, ok}] from AIMessage.tool_calls + their ToolMessages (pass the new messages
    of this tool round). Injected state values never appear in the model's args, so only model args are logged."""
    calls = {tc["id"]: tc for m in messages if isinstance(m, AIMessage) for tc in m.tool_calls}
    out = []
    for m in messages:
        if isinstance(m, ToolMessage) and m.tool_call_id in calls:
            tc = calls[m.tool_call_id]
            out.append(tool_call_record(tc["name"], tc["args"], _ok(m)))
    return out
