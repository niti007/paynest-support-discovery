"""Faithfulness investigation (user decision 2026-11-14): for the lowest min-score drafts, get Nova Lite's DeepEval faithfulness
reasons, extracted claims and verdicts (MASKED text only) so each unsupported claim can be classified by hand:
REAL | JUDGE ERROR | CONTEXT GAP.   uv run python -m solution.evals.faith_investigate [--n 10] [--out file.json]
Judge calls go through judges.cross_judge (ledger-tracked); Nova's 10 RPM quota is handled with retry and backoff."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from deepeval.metrics import FaithfulnessMetric

from solution.evals import judges, spend
from solution.evals.test_deepeval_quality import case

REPORTS = Path(__file__).resolve().parent / "reports"


def lowest_ids(n: int, scores_key: str = "faithfulness_min2") -> list[str]:
    s = json.loads((REPORTS / "p5_deepeval_scores.json").read_text())[scores_key]
    return [i for i, _ in sorted(s.items(), key=lambda kv: (kv[1]["min"], kv[0]))[:n]]


def investigate(row: dict) -> dict:
    last = None
    for attempt in range(5):
        spend.guard()
        m = FaithfulnessMetric(threshold=0.9, model=judges.cross_judge("judge_nova_investigate"), include_reason=True, async_mode=False)
        try:
            m.measure(case(row))
            return {"id": row["id"], "score": m.score, "reason": m.reason, "truths": getattr(m, "truths", None), "claims": getattr(m, "claims", None),
                    "verdicts": [{"verdict": v.verdict, "reason": v.reason} for v in (m.verdicts or [])]}
        except Exception as e:  # noqa: BLE001
            last = repr(e)[:200]
            time.sleep(15 * (attempt + 1))
    return {"id": row["id"], "error": last}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--out", default=str(REPORTS / "faith_investigation_raw.json"))
    a = ap.parse_args()
    rows = {json.loads(x)["id"]: json.loads(x) for x in (REPORTS / "p5_harvest.jsonl").read_text().splitlines() if x.strip()}
    out = []
    for i in lowest_ids(a.n):
        r = investigate(rows[i])
        r.update(masked_text=rows[i]["masked_text"], draft=rows[i]["draft"], tools=rows[i].get("tools"),
                 contexts=[{"source_id": c["source_id"], "body": c["body"]} for c in rows[i]["contexts"]])
        out.append(r)
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
        print(i, r.get("score"), r.get("error"), flush=True)


if __name__ == "__main__":
    main()
