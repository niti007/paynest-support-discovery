"""Phase 3 gate run: the REAL graph on tickets A (PN-100402), B (PN-101055), C (PN-101333) and PN-101129.

Real Decider + Bedrock (Nova Lite / Qwen3-235B, eu-north-1, profile paynest), compose Postgres, the mock payroll API,
the real human_approval / act / log_and_learn nodes. Also shows the interrupt + Command(resume) path and a restart that
resumes from the Postgres checkpoint (ticket A). Masked text only.

    COMPOSE_PROJECT_NAME=a7p3a uv run python -m solution.evals.p3_gate_run
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from langgraph.types import Command

ORACLE = "--oracle" in sys.argv
REPORT = Path(__file__).parent / "reports" / ("phase3_gate_run_oracle.md (merge into phase3_gate_run.md)" if ORACLE else "phase3_gate_run.md")
TICKETS = ["PN-100402", "PN-101055", "PN-101333", "PN-101129"]
EXPECT = {"PN-100402": "draft_for_agent + P1, cites R-UAE-WPS-2026-07 (15 Jul 2026)",
          "PN-101055": "route_queue Payroll-Ops, status tool called",
          "PN-101333": "auto_answer (shadow), KB-0105, no PII echoed",
          "PN-101129": "block, 0 tool calls"}


def start_mock() -> tuple[subprocess.Popen, int]:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "solution.a7.mock_payroll.app:app", "--port", str(port)],
                         env={**os.environ, "A7_PAYROLL_API_TOKEN": "a7-mock-token-dev"},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close(); break
        except OSError:
            time.sleep(0.5)
    os.environ["A7_PAYROLL_API_URL"] = f"http://127.0.0.1:{port}"
    return p, port


class Oracle:
    """Test-only classifier answering the labels a human would (statutory/urgent, payroll/high, howto/normal). Same real LLM/DB/tools/guard."""
    name = "oracle"

    def decide_multi(self, items):
        from solution.a7.decide.base import Decision
        d = lambda c: Decision(c, 0.95, "oracle", 0.0, {})
        txt = items[0].context
        if "IGNORE ALL PREVIOUS" in txt:
            return {"category": d("billing"), "urgency": d("normal"), "is_injection": d("yes")}
        if "WPS" in txt or "SIF" in txt:
            return {"category": d("statutory"), "urgency": d("urgent"), "is_injection": d("no")}
        if "payroll" in txt.lower() and "Processing" in txt:
            return {"category": d("payroll"), "urgency": d("high"), "is_injection": d("no")}
        return {"category": d("howto"), "urgency": d("normal"), "is_injection": d("no")}


def usage_of(backends) -> tuple[int, int, float]:
    tin = tout = 0; cost = 0.0
    for b in backends:
        u = b.usage() if hasattr(b, "usage") else {}
        tin += u.get("tokens_in", 0); tout += u.get("tokens_out", 0); cost += u.get("cost_usd", 0.0)
    return tin, tout, cost


def main() -> None:
    from solution.a7 import db
    from solution.a7.decide.base import get_decider
    from solution.a7.graph import build_graph, postgres_checkpointer, thread_config
    from solution.a7.nodes.normalise import make_normalise
    from solution.a7.nodes.pii_gate import make_pii_gate
    from solution.a7.pii.scanner import scan
    from solution.a7.vault import LocalKeyProvider, PostgresVaultStore, Vault
    from solution.a7.worker import find_record, run_ticket
    from solution.db import bootstrap

    mock, port = start_mock()
    try:
        bootstrap.bootstrap()
        nova = get_decider("bedrock_nova")
        auto = Oracle() if ORACLE else get_decider("decider")
        backends = [nova, auto]
        mk = lambda cp: build_graph(cp, deciders={"decider": auto, "bedrock_nova": nova}, output_decider=nova)
        rows, notes = [], []
        with db.connect(autocommit=True) as vconn, postgres_checkpointer() as cp:
            gate = make_pii_gate(Vault(PostgresVaultStore(vconn), LocalKeyProvider()))
            norm = make_normalise()
            graph = mk(cp)
            for t in TICKETS:
                cp.delete_thread(t)
                vconn.execute("DELETE FROM approvals WHERE ticket_id=%s", (t,))
                vconn.execute("DELETE FROM outbox WHERE ticket_id=%s", (t,))
                b_in, b_out, b_cost = usage_of(backends)
                t0 = time.time()
                v = run_ticket(find_record(t), graph, gate, norm)
                dt = time.time() - t0
                a_in, a_out, a_cost = usage_of(backends)
                ag = [x for x in v.get("trace", []) if x.get("node") == "agent"]
                agent_cost = sum(x["usage"]["cost_usd"] for x in ag if "usage" in x)
                agent_tok = (sum(x["usage"]["tokens_in"] for x in ag if "usage" in x), sum(x["usage"]["tokens_out"] for x in ag if "usage" in x))
                draft = v.get("draft") or ""
                assert not scan(draft, presidio=False), f"PII in draft of {t}"
                rows.append({"t": t, "lane": v.get("lane"), "queue": v.get("queue"), "prio": v.get("priority"), "tenant": v.get("tenant_id"),
                             "cat": v.get("category"), "conf": v.get("category_confidence"), "tier": v.get("llm_tier"),
                             "cites": [(c["source_id"], c.get("effective_from")) for c in v.get("citations") or []],
                             "tools": [(c["name"], c["ok"]) for c in v.get("tool_calls") or []], "guard": (v.get("guard") or {}).get("passed"),
                             "lat": dt, "paused": v.get("__interrupt__"), "approval": (v.get("approval") or {}).get("status"),
                             "actions": [(a.get("type"), a.get("status")) for a in v.get("actions") or []],
                             "reasons": v.get("route_reasons"), "draft": draft,
                             "tok": (agent_tok[0] + a_in - b_in, agent_tok[1] + a_out - b_out), "cost": agent_cost + (a_cost - b_cost)})
                print(t, rows[-1]["lane"], rows[-1]["prio"], rows[-1]["cites"], rows[-1]["tools"], round(dt, 1), flush=True)

            # interrupt + restart + resume on ticket A
            a = rows[0]
            cfg = thread_config("PN-100402")
            snap = graph.get_state(cfg)
            notes.append(f"PN-100402 paused at human_approval after the first run: next={snap.next}, interrupt payload present={bool(a['paused'])}")
            draft_before = snap.values.get("draft")
        with postgres_checkpointer() as cp2:                  # "restart": new connection, new graph object
            g2 = mk(cp2)
            snap2 = g2.get_state(cfg)
            same = snap2.values.get("draft") == draft_before and snap2.next == ("human_approval",)
            notes.append(f"After restart (new Postgres connection + new graph object): next={snap2.next}, same draft={snap2.values.get('draft') == draft_before}")
            g2.invoke(Command(resume={"status": "approved", "reviewer": "gate-reviewer"}), cfg, durability="sync")
            fin = g2.get_state(cfg)
            notes.append(f"Command(resume=approved): next={fin.next}, approval={fin.values['approval']['status']}, "
                         f"actions={[(x.get('type'), x.get('status')) for x in fin.values.get('actions') or []]}, "
                         f"draft unchanged={fin.values.get('draft') == draft_before}")
            same = same and fin.next == () and fin.values.get("draft") == draft_before

        tot = sum(r["cost"] for r in rows)
        out = ["# Phase 3 gate run (real graph, real Bedrock, mock payroll API)", "",
               f"Classifier: {'ORACLE (test-only labels a human would give; boxes 6-10 proof)' if ORACLE else 'REAL Decider (honest run)'}. "
               f"Run {time.strftime('%Y-%m-%d %H:%M')} local. Region eu-north-1, profile paynest, routing threshold "
               f"0.90, AUTO_SEND=false. Masked text only.", "",
               "| ticket | expected | lane | queue | priority | tenant | category (conf) | tier | citations | tool calls | guard | latency s | tokens in/out | USD |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            out.append(f"| {r['t']} | {EXPECT[r['t']]} | {r['lane']} | {r['queue']} | {r['prio']} | {r['tenant']} | {r['cat']} ({r['conf']}) | "
                       f"{r['tier']} | {r['cites']} | {r['tools']} | {r['guard']} | {r['lat']:.1f} | {r['tok'][0]}/{r['tok'][1]} | {r['cost']:.5f} |")
        out += ["", f"Total Bedrock cost (classifier + agent + guard): USD {tot:.5f}", "", "## Per ticket", ""]
        for r in rows:
            out += [f"### {r['t']}", f"- approval: {r['approval']}; actions: {r['actions']}; paused at interrupt: {r['paused']}",
                    f"- route reasons: {r['reasons']}", "- draft (masked):", "", "```", r["draft"] or "(none)", "```", ""]
        out += ["## Interrupt, resume and restart", ""] + [f"- {n}" for n in notes] + [f"- restart resumes same draft: {same}", ""]
        REPORT.write_text("\n".join(out))
        print("\n".join(notes))
        print(f"total cost USD {tot:.5f}; report -> {REPORT}")
    finally:
        mock.terminate()


if __name__ == "__main__":
    main()
