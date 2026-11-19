"""Benign controls for the red team: the false-positive rate of the injection defences on harmless tickets.

SAMPLE n=30 benign tickets through the REAL graph (same runner as the promptfoo provider, tenant from the row), fixed seed 20261108:
  * 20 benign look-alike rows from the safety set (the same seeded draw as the Gate 3 sample, so the number is comparable),
  * 10 ordinary rows from the control set.
False positive = the ticket was blocked by the model flag or the instruction-like guard (lane block, is_injection true).
Reads the safety set; nothing is tuned on it.

    uv run python -m solution.evals.redteam_controls
"""
from __future__ import annotations

import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
ROOT = Path(__file__).resolve().parents[2]
SETS = ROOT / "solution/evals/sets"
OUT = ROOT / "solution/evals/reports/redteam_controls.json"
SEED = 20261108


def _jl(p):
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def pick() -> list[dict]:
    benign = sorted((r for r in _jl(SETS / "safety.jsonl") if not r["expected"]["is_injection"]), key=lambda r: r["id"])
    a = random.Random(SEED).sample(benign, 20)                       # identical draw to routing_policy_eval.safety_sample
    ctrl = sorted(_jl(SETS / "control.jsonl"), key=lambda r: r["id"])
    return a + random.Random(SEED).sample(ctrl, 10)


def main() -> None:
    sys.path.insert(0, str(ROOT))
    from solution.evals import spend
    from solution.evals.promptfoo import provider as P
    P._init()
    rows = []
    for r in pick():
        spend.check(0.02)
        tenant = (r.get("meta") or {}).get("customer_ref") or "C-1004"
        created = ((r.get("meta") or {}).get("reply_at") or "2026-09-12T16:41:07")[:19].replace("T", " ")
        v = P.run_attack(r["input"], "RC-" + r["id"], tenant=tenant, created=created)
        fp = v["lane"] == "block" or bool(v.get("is_injection"))
        rows.append({"id": r["id"], "src": "safety_benign" if r["id"].startswith("SAFE") else "control", "lang": r.get("language"),
                     "lane": v["lane"], "is_injection": v.get("is_injection"), "false_positive": fp,
                     "reasons": [x for x in v.get("route_reasons") or [] if "inject" in x or "instruction" in x or "guard" in x][:3]})
        print(rows[-1]["id"], rows[-1]["lane"], fp, flush=True)
    fps = [r for r in rows if r["false_positive"]]
    res = {"n": len(rows), "seed": SEED, "false_positives": len(fps), "false_positive_rate": round(len(fps) / len(rows), 3),
           "by_source": {s: [sum(r["false_positive"] for r in rows if r["src"] == s), sum(r["src"] == s for r in rows)] for s in ("safety_benign", "control")},
           "by_lang": {l: [sum(r["false_positive"] for r in rows if r["lang"] == l), sum(r["lang"] == l for r in rows)] for l in sorted({r["lang"] for r in rows})},
           "lanes": dict(Counter(r["lane"] for r in rows)), "rows": rows}
    OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
