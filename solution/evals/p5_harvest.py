"""Phase 5 harvest: run the REAL graph (Nova routes, Qwen3-235B drafts, compose Postgres/Valkey, mock payroll API) on a fixed-seed
SAMPLE of the eval sets and write one masked record per ticket to reports/p5_harvest.jsonl. The pytest gates read this file; the
judges and RAGAS never see anything but these masked fields.

    solution/evals/p5-run: see p5a/run.sh (env: A7_*_DATABASE_URL, A7_VALKEY_URL, AWS_PROFILE=paynest)
    uv run python -m solution.evals.p5_harvest [--stale 30] [--control 20] [--smoke]

SAMPLES, not full sets: stale n=30 of 48, control n=20 of 29, smoke all 14 (seed 20261108, random.Random(seed).sample over sorted ids).
An approved draft_for_agent whose guard passed is resumed with a reviewer decision (so act/outbox/checkpoint paths are exercised and
the living Langfuse dataset has approved drafts). Spend is tracked in reports/p5_spend.json; the run stops at the stop line.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

from langgraph.types import Command

ROOT = Path(__file__).resolve().parents[2]
SETS = ROOT / "solution/evals/sets"
OUT = ROOT / "solution/evals/reports/p5_harvest.jsonl"
SEED = 20261108
HARVEST_BUDGET_USD = 0.35       # of the 1.00 total; judges and RAGAS get the rest


def jl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def sample_ids(name: str, n: int | None) -> list[str]:
    ids = sorted(r["id"] for r in jl(SETS / f"{name}.jsonl"))
    return ids if n is None or n >= len(ids) else sorted(random.Random(SEED).sample(ids, n))


def plan(n_stale: int, n_control: int, smoke: bool) -> list[tuple[str, str]]:
    out, seen = [], set()
    for name, n in (("stale", n_stale), ("control", n_control), ("smoke", None if smoke else 0)):
        if n == 0:
            continue
        for i in sample_ids(name, n):
            if i not in seen:
                seen.add(i)
                out.append((name, i))
    return out


def start_mock() -> subprocess.Popen:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "solution.a7.mock_payroll.app:app", "--port", str(port)],
                         env={**os.environ, "A7_PAYROLL_API_TOKEN": "a7-mock-token-dev"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close(); break
        except OSError:
            time.sleep(0.5)
    os.environ["A7_PAYROLL_API_URL"] = f"http://127.0.0.1:{port}"
    return p


def usage_of(backends) -> tuple[int, int, float]:
    tin = tout = 0; cost = 0.0
    for b in backends:
        u = b.usage()
        tin += u.get("tokens_in", 0); tout += u.get("tokens_out", 0); cost += u.get("cost_usd", 0.0)
    return tin, tout, cost


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale", type=int, default=30)
    ap.add_argument("--control", type=int, default=20)
    ap.add_argument("--no-smoke", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="ignore an existing p5_harvest.jsonl")
    a = ap.parse_args()

    from solution.a7 import db
    from solution.a7.decide.base import get_decider
    from solution.a7.graph import build_graph, postgres_checkpointer, thread_config
    from solution.a7.nodes.normalise import make_normalise
    from solution.a7.nodes.pii_gate import make_pii_gate
    from solution.a7.pii.scanner import scan
    from solution.a7.vault import LocalKeyProvider, PostgresVaultStore, Vault
    from solution.a7.worker import find_record, run_ticket
    from solution.db import bootstrap
    from solution.evals import spend

    todo = plan(a.stale, a.control, not a.no_smoke)
    done = set()
    if OUT.exists() and not a.fresh:
        done = {r["id"] for r in jl(OUT)}
    elif OUT.exists():
        OUT.unlink()
    mock = start_mock()
    try:
        bootstrap.bootstrap()
        nova, qwen = get_decider("bedrock_nova"), get_decider("bedrock_qwen32b")
        with db.connect(autocommit=True) as vconn, postgres_checkpointer() as cp, open(OUT, "a") as f:
            gate = make_pii_gate(Vault(PostgresVaultStore(vconn), LocalKeyProvider()))
            norm = make_normalise()
            graph = build_graph(cp, deciders={"bedrock_nova": nova, "bedrock_qwen32b": qwen}, output_decider=nova)
            spent = 0.0
            for n, (setname, tid) in enumerate(todo, 1):
                if tid in done:
                    continue
                if spent >= HARVEST_BUDGET_USD:
                    print(f"harvest budget {HARVEST_BUDGET_USD} reached at {n - 1}/{len(todo)}; stopping", flush=True)
                    break
                spend.guard()
                cp.delete_thread(tid)
                for tbl in ("approvals", "outbox"):
                    vconn.execute(f"DELETE FROM {tbl} WHERE ticket_id=%s", (tid,))
                b_in, b_out, b_cost = usage_of([nova, qwen])
                t0 = time.time()
                v = run_ticket(find_record(tid), graph, gate, norm)
                dt = time.time() - t0
                a_in, a_out, a_cost = usage_of([nova, qwen])
                ag = [x for x in v.get("trace", []) if x.get("node") == "agent" and "usage" in x]
                agent_cost = sum(x["usage"]["cost_usd"] for x in ag)
                agent_tok = (sum(x["usage"]["tokens_in"] for x in ag), sum(x["usage"]["tokens_out"] for x in ag))
                cost = agent_cost + (a_cost - b_cost)
                spent += cost
                spend.add("harvest", cost, calls=1)
                draft = v.get("draft") or ""
                approval = None
                if v.get("__interrupt__") and v.get("lane") == "draft_for_agent" and (v.get("guard") or {}).get("passed"):
                    cfg = thread_config(tid)
                    graph.invoke(Command(resume={"status": "approved", "reviewer": "eval-reviewer"}), cfg, durability="sync")
                    approval = "approved"
                rec = {"set": setname, "id": tid, "lane": v.get("lane"), "queue": v.get("queue"), "priority": v.get("priority"),
                       "category": v.get("category"), "language": v.get("language"), "tenant": v.get("tenant_id"),
                       "as_of": v.get("as_of") or (v.get("received_at") or "")[:10], "received_at": v.get("received_at"),
                       "masked_text": v.get("masked_text"), "draft": draft,
                       "contexts": [{"source_id": c["source_id"], "body": c.get("body", ""), "effective_from": c.get("effective_from")}
                                    for c in v.get("context_chunks") or []],
                       "retrieved": v.get("retrieved") or [], "citations": v.get("citations") or [],
                       "tools": [(c["name"], c["ok"]) for c in v.get("tool_calls") or []],
                       "guard": v.get("guard"), "cache": (v.get("cache") or {}).get("status"), "route_reasons": v.get("route_reasons"),
                       "classify_backend": next((x.get("backend") for x in v.get("trace", []) if x.get("node") == "classify"), None),
                       "steps": [x.get("node") for x in v.get("trace", [])], "tier": v.get("llm_tier"),
                       "latency_s": round(dt, 2), "tokens_in": agent_tok[0] + a_in - b_in, "tokens_out": agent_tok[1] + a_out - b_out,
                       "agent_tokens": list(agent_tok), "agent_model": ag[-1]["usage"]["model"] if ag else None,
                       "cost_usd": round(cost, 6), "paused": bool(v.get("__interrupt__")), "approval": approval,
                       "draft_pii_hits": [h.type for h in scan(draft, presidio=True, ner=False)]}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
                print(f"{n}/{len(todo)} {tid} {setname} lane={rec['lane']} {rec['latency_s']}s ${cost:.5f} total_harvest=${spent:.4f}", flush=True)
    finally:
        mock.terminate()


if __name__ == "__main__":
    main()
