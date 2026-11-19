"""Run a decide() backend over eval sets and write solution/evals/reports/preds_<backend>_<set>.jsonl.

  uv run python solution/evals/run_decider.py --sets routing,safety,stale,control,incident,smoke [--backend decider] [--limit 50]

Each record gets ONE decide_multi call (category, urgency, is_injection) on the masked input text, so the backend
can answer all three questions in a single forward pass / call. Inputs are masked text only; nothing is printed
except ids and counts. Output rows: id, name, choice, confidence, latency_ms.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SETS_DIR = ROOT / "solution" / "evals" / "sets"
REPORTS = ROOT / "solution" / "evals" / "reports"

def load_set(name: str) -> list[dict]:
    with open(SETS_DIR / f"{name}.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run_set(backend, backend_name: str, set_name: str, limit: int | None = None, out: Path | None = None) -> dict:
    from solution.a7.decide.base import classify_questions

    records = load_set(set_name)
    if limit:
        records = records[:limit]
    out = out or REPORTS / f"preds_{backend_name}_{set_name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    n_invalid = 0
    with open(out, "w", encoding="utf-8") as f:
        for r in records:
            text = r["input"]
            # integration: the shared box-4 questions/options, identical for every backend
            res = backend.decide_multi(classify_questions({"masked_text": text}))
            for name, d in res.items():
                n_invalid += d.choice == "__invalid__"
                f.write(json.dumps({"id": r["id"], "name": name, "choice": d.choice,
                                    "confidence": round(d.confidence, 4), "latency_ms": round(d.latency_ms, 2)}) + "\n")
    wall = time.perf_counter() - t0
    return {"set": set_name, "n_tickets": len(records), "wall_s": round(wall, 1),
            "s_per_ticket": round(wall / max(1, len(records)), 3), "invalid": n_invalid, "path": str(out)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="routing,safety,stale,control,incident,smoke")
    ap.add_argument("--backend", default="decider")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    if args.backend == "decider":
        from solution.a7.decide.decider import StrandsDeciderBackend

        backend = StrandsDeciderBackend()
    else:
        from solution.a7.decide import get_decider

        backend = get_decider(args.backend)
    for s in args.sets.split(","):
        if not (SETS_DIR / f"{s}.jsonl").exists():
            print(f"skip {s}: no set file")
            continue
        print(json.dumps(run_set(backend, args.backend, s, args.limit)), flush=True)


if __name__ == "__main__":
    main()
