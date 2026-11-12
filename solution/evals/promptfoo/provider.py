"""promptfoo Python provider: runs ONE ticket through the REAL A7 graph and returns a JSON verdict (masked data only).

    prompt  -> the attack text, put into a widget ticket of tenant C-1004 (Falcon)
    gate    -> pii_gate + normalise run first (the attack text is masked before anything else sees it)
    graph   -> real classify (Nova Lite, Qwen3-32B fallback), dedupe, route, retrieve, cache, agent (Nova / Qwen3-235B), guard,
               human_approval (pauses at the interrupt, nobody approves), real read-only tenant-locked tools via the mock payroll API

Output JSON: lane, is_injection, route_reasons, draft (masked), tool_calls (name + masked args), outbox rows (kind/status/shadow/sent_at), cost.
Env: A7_* DSNs, AWS creds (see pf.sh), A7_REDTEAM_TENANT (default C-1004), A7_SPEND_LEDGER, A7_GUARD_BREAK=1 (CI red demo only).
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
TENANT = os.environ.get("A7_REDTEAM_TENANT", "C-1004")
_ctx: dict = {}


def _start_mock() -> None:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "solution.a7.mock_payroll.app:app", "--port", str(port)],
                         env={**os.environ, "A7_PAYROLL_API_TOKEN": "a7-mock-token-dev"}, cwd=str(ROOT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close(); break
        except OSError:
            time.sleep(0.5)
    os.environ["A7_PAYROLL_API_URL"] = f"http://127.0.0.1:{port}"
    _ctx["mock"] = p


def _init() -> None:
    if _ctx:
        return
    from langgraph.checkpoint.memory import MemorySaver

    from solution.a7 import db
    from solution.a7.decide.base import get_decider
    from solution.a7.graph import build_graph
    from solution.a7.nodes.normalise import make_normalise
    from solution.a7.nodes.pii_gate import make_pii_gate
    from solution.a7.vault import LocalKeyProvider, MemoryVaultStore, Vault
    from solution.db import bootstrap

    _start_mock()
    bootstrap.bootstrap()
    nova, qwen = get_decider("bedrock_nova"), get_decider("bedrock_qwen32b")
    _ctx.update(graph=build_graph(MemorySaver(), deciders={"bedrock_nova": nova, "bedrock_qwen32b": qwen}, output_decider=nova),
                gate=make_pii_gate(Vault(MemoryVaultStore(), LocalKeyProvider())), norm=make_normalise(),
                conn=db.connect(autocommit=True), backends=[nova, qwen])


def _backend_cost() -> float:
    return sum((b.usage() if hasattr(b, "usage") else {}).get("cost_usd", 0.0) for b in _ctx["backends"])


def run_attack(text: str, ticket_id: str, tenant: str = TENANT, created: str = "2026-09-12 16:41:07", with_context: bool = False) -> dict:
    from solution.a7.graph import thread_config
    from solution.a7.worker import prepare
    from solution.evals import spend

    spend.check(0.01)
    rec = {"tkt": ticket_id, "source": "inapp_widget", "org_id": tenant, "user_ref": "U-RT", "title": "Support request", "message": text,
           "cat": "Other", "prio": "normal", "state": "open", "created": created, "first_reply_ts": None, "closed_ts": None, "history": []}
    c0 = _backend_cost()
    st = prepare(rec, _ctx["gate"], _ctx["norm"])
    out: dict = {"ticket": ticket_id, "tenant": tenant, "gate": st.get("pii_gate_status")}
    if st.get("pii_gate_status") != "passed":
        out.update(lane="human_review", route_reasons=["pii_gate_blocked"], draft="", tool_calls=[], outbox=[], is_injection=None)
    else:
        cfg = thread_config(ticket_id)
        _ctx["graph"].invoke(st, cfg, durability="sync")
        v = dict(_ctx["graph"].get_state(cfg).values)
        agent_cost = sum(x["usage"]["cost_usd"] for x in v.get("trace", []) if x.get("node") == "agent" and "usage" in x)
        rows = _ctx["conn"].execute("SELECT kind, status, shadow, sent_at IS NOT NULL FROM outbox WHERE ticket_id=%s", (ticket_id,)).fetchall()
        out.update(lane=v.get("lane"), is_injection=bool(v.get("is_injection")), route_reasons=v.get("route_reasons"), category=v.get("category"),
                   draft=v.get("draft") or "", guard=(v.get("guard") or {}).get("passed"),
                   tool_calls=[{"name": t.get("name"), "args": t.get("args_masked") or t.get("args"), "ok": t.get("ok")} for t in v.get("tool_calls") or []],
                   outbox=[{"kind": r[0], "status": r[1], "shadow": r[2], "sent": r[3]} for r in rows],
                   context=[c.get("body") for c in v.get("context_chunks") or []] if with_context else None,
                   actions=[(a.get("type"), a.get("status")) for a in v.get("actions") or []], agent_cost=agent_cost)
    out["cost_usd"] = round(_backend_cost() - c0 + out.get("agent_cost", 0.0), 6)
    spend.add("redteam_graph", out["cost_usd"], ticket=ticket_id)
    return out


def call_api(prompt, options, context):
    _init()
    tid = "RT-" + hashlib.sha1(prompt.encode()).hexdigest()[:10]
    try:
        out = run_attack(prompt, tid)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return {"output": json.dumps(out, ensure_ascii=False, default=str), "cost": out["cost_usd"]}
