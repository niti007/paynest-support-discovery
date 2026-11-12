"""Sweep T_link / T_inc / W on the TUNING split only, then report the chosen setting on the EVAL split.

Splits (fixed seed 20261108; Meridian and the safety set are never used for tuning):
  routing tickets = routing.jsonl minus the 78 incident-set tickets (1,249); 50% sample by seeded shuffle.
  TUNING = Kestrel PNJ-4412 (48) + routing sample A.
  EVAL   = Meridian (30) + the 26 resubmits + routing sample B (the other 50%).
  Context-only tickets (not scored): parents of the 26 resubmits are added to the eval stream so links can resolve.
Each split is replayed as its own time-ordered stream, using the same pure `decide` as the graph node.

Usage: uv run python solution/evals/dedupe_sweep.py [--write-report]
"""
from __future__ import annotations

import itertools
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE.parent))

from dedupe_corpus import load  # noqa: E402
from solution.a7.nodes.dedupe_cluster import Cand, Params, decide, parse_ts  # noqa: E402

SEED = 20261108
GATE_FIFTH = "PN-101239"


def splits(corpus: list[dict]) -> dict:
    rng = random.Random(SEED)
    plain = sorted(r["id"] for r in corpus if r["routing"] and not r["incident"])
    rng.shuffle(plain)
    half = len(plain) // 2
    a, b = set(plain[:half]), set(plain[half:])
    kest = {r["id"] for r in corpus if r["incident"] == "PNJ-4412"}
    mer = {r["id"] for r in corpus if r["incident"] == "meridian-2026-09-25"}
    resub = {r["id"] for r in corpus if r["parent"]}
    parents = {r["parent"] for r in corpus if r["parent"]}
    return {"tuning": {"scored": a | kest, "stream": a | kest}, "eval": {"scored": b | mer | resub, "stream": b | mer | resub | parents},
            "all": {"scored": {r["id"] for r in corpus}, "stream": {r["id"] for r in corpus}},
            "sets": {"routing_A": a, "routing_B": b, "kestrel": kest, "meridian": mer, "resub": resub}}


def sims_for(corpus, vecs):
    idx = {r["id"]: i for i, r in enumerate(corpus)}
    n = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9)
    return idx, n


def replay(corpus, idx, nvecs, stream_ids, p: Params) -> dict:
    """Replay in time order per tenant. Returns {ticket_id: Decision} (incident ids are local ints)."""
    by_t = defaultdict(list)
    for r in corpus:
        if r["id"] in stream_ids and r["tenant"]:
            by_t[r["tenant"]].append(r)
    out, nxt = {}, itertools.count(1)
    horizon = max(p.link_days * 24, p.window_hours)
    for rows in by_t.values():
        state: dict[str, tuple] = {}          # id -> (ts, parent, incident_id)
        ts_list = [(parse_ts(r["received_at"]), r["id"]) for r in rows]
        for k, r in enumerate(rows):
            ts = ts_list[k][0]
            cands = []
            for j in range(k - 1, -1, -1):
                tj, idj = ts_list[j]
                if (ts - tj).total_seconds() / 3600 > horizon:
                    break
                s = float(nvecs[idx[idj]] @ nvecs[idx[r["id"]]])
                cands.append(Cand(idj, tj, s, state[idj][1], state[idj][2]))
            d = decide(ts, cands, p)
            inc = d.incident_id
            if d.incident and inc is None:
                inc = next(nxt)
                for cid in d.cluster_ids:
                    t0, _par, _ = state[cid]
                    state[cid] = (t0, None, inc)
            state[r["id"]] = (ts, d.parent_ticket_id, inc if d.incident else None)
            d.incident_id = inc if d.incident else None
            out[r["id"]] = d
    return out


def metrics(corpus, decisions, scored: set, sets: dict) -> dict:
    byid = {r["id"]: r for r in corpus}
    plain = (sets["routing_A"] | sets["routing_B"]) & scored
    m = {}
    m["routing_tickets"] = len(plain)
    m["false_incident_tickets"] = sum(1 for i in plain if decisions[i].incident)
    m["false_incident_clusters"] = len({decisions[i].incident_id for i in plain if decisions[i].incident})
    m["false_links_routing"] = sum(1 for i in plain if decisions[i].parent_ticket_id)
    kest = sets["kestrel"] & scored
    if kest:
        m["kestrel_total"] = len(kest)
        m["kestrel_in_incident"] = sum(1 for i in kest if decisions[i].incident)
        m["kestrel_false_links"] = sum(1 for i in kest if decisions[i].parent_ticket_id)
    mer = sets["meridian"] & scored
    if mer:
        order = sorted(mer, key=lambda i: byid[i]["received_at"])
        flagged = [i for i in order if decisions[i].incident]
        m["meridian_total"] = len(mer)
        m["meridian_in_incident"] = len(flagged)
        m["meridian_first_flagged"] = flagged[0] if flagged else None
        m["meridian_first_flagged_rank"] = order.index(flagged[0]) + 1 if flagged else None
        m["meridian_opened_by_5th_ticket"] = bool(flagged) and order.index(flagged[0]) < 5 and flagged[0] in order[:5]
        m["meridian_incident_clusters"] = len({decisions[i].incident_id for i in flagged})
        m["meridian_false_links"] = sum(1 for i in mer if decisions[i].parent_ticket_id)
    res = sets["resub"] & scored
    if res:
        ok = sum(1 for i in res if decisions[i].parent_ticket_id == byid[i]["parent"])
        wrong = sum(1 for i in res if decisions[i].parent_ticket_id and decisions[i].parent_ticket_id != byid[i]["parent"])
        m["resubmits_total"] = len(res)
        m["resubmits_linked_correct"] = ok
        m["resubmits_linked_wrong_parent"] = wrong
        m["resubmits_in_incident"] = sum(1 for i in res if decisions[i].incident)
    return m


def main() -> int:
    corpus, vecs = load()
    sp = splits(corpus)
    idx, nv = sims_for(corpus, vecs)
    byid = {r["id"]: r for r in corpus}
    # similarity diagnostics (resubmit -> parent cosine) are eval-only information, printed for the report
    rs = [(i, float(nv[idx[i]] @ nv[idx[byid[i]["parent"]]])) for i in sorted(sp["sets"]["resub"])]
    print("resubmit->parent cosine: min %.3f median %.3f max %.3f" % (min(s for _, s in rs), np.median([s for _, s in rs]), max(s for _, s in rs)))

    Wg = [1, 2, 4, 6, 12, 24, 48, 72, 168]
    Ti = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    Tl = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    rows = []
    for W, ti in itertools.product(Wg, Ti):
        d = replay(corpus, idx, nv, sp["tuning"]["stream"], Params(t_inc=ti, window_hours=W, t_link=2.0))
        m = metrics(corpus, d, sp["tuning"]["scored"], sp["sets"])
        rows.append({"W": W, "t_inc": ti, **{k: m[k] for k in ("false_incident_tickets", "false_incident_clusters", "kestrel_in_incident")}})
    print("INC sweep (tuning): W t_inc false_inc_tickets clusters kestrel_in_incident/48")
    for r in rows:
        print(r["W"], r["t_inc"], r["false_incident_tickets"], r["false_incident_clusters"], r["kestrel_in_incident"])
    lrows = []
    for tl in Tl:
        d = replay(corpus, idx, nv, sp["tuning"]["stream"], Params(t_inc=2.0, t_link=tl))
        m = metrics(corpus, d, sp["tuning"]["scored"], sp["sets"])
        lrows.append({"t_link": tl, "false_links_routing": m["false_links_routing"], "kestrel_false_links": m["kestrel_false_links"]})
        print("LINK", tl, m["false_links_routing"], m["kestrel_false_links"])
    json.dump({"inc": rows, "link": lrows}, open(HERE.parent / "cache" / "dedupe_sweep.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
