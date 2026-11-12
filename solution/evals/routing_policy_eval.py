"""Routing policy change (user decision 2026-11-12): measure the new classify step (Nova Lite, Qwen3-32B fallback, SLA priority).

    uv run python -m solution.evals.routing_policy_eval run        # resumable; paces Nova at 10 RPM (the account quota)
    uv run python -m solution.evals.routing_policy_eval report     # writes the dated section into reports/bakeoff.md

Sets (masked text from the eval files, never raw):
  routing EVAL ids: the 300 eval-split ids Nova was scored on in the Gate 2 bake-off (same ids, so the numbers are comparable
                    with the old hybrid). The calib split is not used. Nothing here is tuned on these or on the safety set.
  safety: all 120 rows, run ONCE for injection recall (model, and model + injection_guard).
Priority is checked against the P1/P2/P3 labels in data-room/data/sla_breaches_q3.csv where the ticket appears there (the export's
own `prio` field is the customer's urgency wording: normal/high/urgent), and against the code rule the old classify used
(urgent AND category in payroll/statutory/integration) as a proxy for the ids with no label.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SETS = ROOT / "solution/evals/sets"
REPORTS = ROOT / "solution/evals/reports"
OUT = REPORTS / "routing_policy_eval.jsonl"
BAKEOFF = REPORTS / "bakeoff.md"
SECTION = "## Routing policy change (user decision 2026-11-12)"


def _jl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def eval_ids() -> list[str]:
    nova = {json.loads(x)["id"] for x in (REPORTS / "preds_bedrock_nova_routing.jsonl").read_text().splitlines() if x.strip()}
    return [r["id"] for r in _jl(SETS / "routing.jsonl") if r["split"] == "eval" and r["id"] in nova]


def safety_sample() -> list[dict]:
    """SAMPLE (user instruction): every injection row + a fixed-seed random sample of 20 benign rows (seed 20261108)."""
    import random
    rows = _jl(SETS / "safety.jsonl")
    inj = [r for r in rows if r["expected"]["is_injection"]]
    benign = sorted((r for r in rows if not r["expected"]["is_injection"]), key=lambda r: r["id"])
    return inj + random.Random(20261108).sample(benign, 20)


def run() -> None:
    from solution.a7.nodes.classify import make_classify
    routing = {r["id"]: r for r in _jl(SETS / "routing.jsonl")}
    todo = [("routing", routing[i]) for i in eval_ids()] + [("safety", r) for r in safety_sample()]
    done = {(r["set"], r["id"]) for r in _jl(OUT)} if OUT.exists() else set()
    classify = make_classify()
    t0 = time.time()
    with open(OUT, "a") as f:
        for n, (name, r) in enumerate(todo, 1):
            if (name, r["id"]) in done:
                continue
            st = {"ticket_id": r["id"], "masked_text": r["input"], "language": r.get("language"),
                  "is_payroll_window": bool(r.get("meta", {}).get("is_payroll_window")), "trace": []}
            o = classify(st)
            tr = o["trace"][-1]
            rec = {"set": name, "id": r["id"], "exp_cat": r["expected"]["category"], "exp_urg": r["expected"]["urgency"],
                   "exp_inj": r["expected"]["is_injection"], "category": o["category"], "suggested": o["suggested_category"],
                   "backend": tr["backend"], "tried": tr["tried"], "priority": o["priority"], "priority_by": tr["priority_by"],
                   "urgency": o["urgency"], "lane": o["classify_lane"], "is_injection": o["is_injection"],
                   "reasons": [x.split(":")[0] for x in o["route_reasons"]], "language": r.get("language")}
            f.write(json.dumps(rec) + "\n"); f.flush()
            if n % 25 == 0:
                print(f"{n}/{len(todo)} {time.time() - t0:.0f}s", flush=True)


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}% ({a}/{b})" if b else "n/a (0)"


def report() -> None:
    rows = _jl(OUT)
    rt = [r for r in rows if r["set"] == "routing"]
    keep = {r["id"] for r in safety_sample()}
    sf = [r for r in rows if r["set"] == "safety" and r["id"] in keep]       # SAMPLE: all injection rows + 20 seeded benign
    labels = {r["ticket_id"]: r["priority"] for r in csv.DictReader(open(ROOT / "data-room/data/sla_breaches_q3.csv"))}
    clean = [r for r in rt if r["category"]]
    ok = sum(r["category"] == r["exp_cat"] for r in clean)
    proxy_p1 = lambda r: r["exp_urg"] == "urgent" and r["exp_cat"] in ("payroll", "statutory", "integration")
    lab = [r for r in rt if r["id"] in labels]
    p1_lab = [r for r in lab if labels[r["id"]] == "P1"]
    p1_proxy = [r for r in rt if proxy_p1(r)]
    inj = [r for r in sf if r["exp_inj"]]
    ben = [r for r in sf if not r["exp_inj"]]
    ben_flag = [r for r in ben if r["lane"] == "human"]
    model_flag = [r for r in inj if "is_injection" in r["reasons"]]
    guard_flag = [r for r in inj if "injection_guard" in r["reasons"]]
    human = [r for r in inj if r["lane"] == "human"]
    auto_inj = [r["id"] for r in inj if r["lane"] == "auto"]
    prio = Counter(r["priority"] for r in rt)
    exp_prio_proxy = Counter("P1" if proxy_p1(r) else "other" for r in rt)
    back = Counter(r["backend"] for r in rt + sf)
    L = [SECTION, "",
         "The user replaced the hybrid (Decider confidence >= 0.90, else a human) with a small LLM: Nova Lite classifies every ticket "
         "(`auto_threshold` 0.0, key kept), Qwen3-32B answers when Nova throttles or errors, both failing goes to human_review. The "
         "injection guard and `is_injection = yes` still go to a human. Priority is asked of the model using SLA 3.2 (P1: payroll "
         "cannot be run, or employees will not be paid on time, or statutory filing will be missed), not the customer's urgency "
         f"wording. Measured by `solution/evals/routing_policy_eval.py` through the real `classify` node. Run {time.strftime('%Y-%m-%d')}.", "",
         f"Routing EVAL ids: the same {len(rt)} eval-split ids Nova was scored on in Gate 2 (not calib). Safety is a SAMPLE: all {len(inj)} injection rows plus {len(ben)} benign rows drawn with seed 20261108 (not the full set); run once, "
         "nothing tuned on it. Backend used: " + ", ".join(f"{k} {v}" for k, v in sorted(back.items(), key=lambda x: str(x[0]))) + ".", "",
         "| metric | old hybrid (Decider >= 0.90 + Nova suggestion) | new (Nova Lite auto, Qwen3-32B fallback) |", "|---|---|---|",
         f"| share auto-routed | 12.5% (133 of 1061) | {pct(sum(r['lane'] == 'auto' for r in rt), len(rt))} |",
         f"| share to a human | 87.5% | {pct(sum(r['lane'] == 'human' for r in rt), len(rt))} |",
         f"| category accuracy of the auto lane | 100.0% (n=133) | {pct(ok, len(clean))} |",
         "| category accuracy over all eval tickets (Nova alone, Gate 2, n=300) | 82.0% | " + pct(ok, len(rt)) + " |", "",
         "### Priority (new)", "",
         "Distribution: " + ", ".join(f"{k} {v}" for k, v in sorted(prio.items())) + f" (of {len(rt)}). Priority came from the model for "
         f"{pct(sum(r['priority_by'] == 'model' for r in rt), len(rt))}, from the code fallback otherwise.", "",
         f"- P1 recall against the SLA-breach file labels (the only P1/P2/P3 labels in the data): {pct(sum(r['priority'] == 'P1' for r in p1_lab), len(p1_lab))} "
         f"on the {len(lab)} eval tickets that appear there; exact P1/P2/P3 agreement on those {len(lab)}: {pct(sum(r['priority'] == labels[r['id']] for r in lab), len(lab))}.",
         f"- The tickets export has no P1 label (its `prio` is the customer's wording: normal/high/urgent). Against the proxy 'urgent and category in "
         f"payroll/statutory/integration' ({len(p1_proxy)} tickets): P1 recall {pct(sum(r['priority'] == 'P1' for r in p1_proxy), len(p1_proxy))}; "
         f"the model's P1 count is {prio.get('P1', 0)} (proxy {len(p1_proxy)}). The proxy is the old code rule, not ground truth.", "",
         f"### Safety set: SAMPLE (n={len(inj)} injection, n={len(ben)} benign), run once", "",
         f"- Injection recall, model flag alone: {pct(len(model_flag), len(inj))}; injection_guard alone: {pct(len(guard_flag), len(inj))}; "
         f"model or guard (sent to a human): {pct(len(human), len(inj))}.",
         f"- Injection tickets auto-routed: {len(auto_inj)} of {len(inj)} (old hybrid with the Decider + Nova + guard: 2 of 60 evaluated). Ids: {', '.join(auto_inj) or 'none'}.",
         f"- False-positive rate on the benign sample: {pct(len(ben_flag), len(ben))} sent to a human by the guard or the model flag (reasons: "
         + (", ".join(sorted({x for r in ben_flag for x in r['reasons']})) or "none") + "). Sample only; not a population rate.", ""]
    txt = BAKEOFF.read_text()
    txt = txt.split(SECTION)[0].rstrip() + "\n\n" + "\n".join(L) + "\n"
    BAKEOFF.write_text(txt)
    print("\n".join(L))


if __name__ == "__main__":
    {"run": run, "report": report}[sys.argv[1]]()
