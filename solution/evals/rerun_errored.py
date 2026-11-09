"""Re-run ONLY the errored tickets of a Bedrock preds file and merge the clean results back (by id, name).

  AWS_PROFILE=paynest uv run python solution/evals/rerun_errored.py bedrock_qwen32b routing
  AWS_PROFILE=paynest uv run python solution/evals/rerun_errored.py bedrock_nova routing-300     # first run: all ids of the subset
  AWS_PROFILE=paynest uv run python solution/evals/rerun_errored.py bedrock_nova safety

Fail fast: a credential error (LoginRefreshRequired / Expired token / UnrecognizedClient) stops the run; nothing is
written for the failing tickets and no error row is ever recorded. Tickets already completed are merged first.
"""
from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from solution.a7.decide import get_decider  # noqa: E402
from solution.a7.decide.base import classify_questions  # noqa: E402
from solution.a7.decide.bedrock_enum import CredentialError  # noqa: E402
from solution.evals.prelim_preds import REPORTS, select  # noqa: E402

WORKERS = {"bedrock_qwen32b": 6, "bedrock_nova": 3}


def main(backend: str, set_name: str) -> int:
    name, recs = select(set_name)
    out_name = name.split("-")[0] if name.startswith("routing-") else name
    path = REPORTS / f"preds_{backend}_{out_name}.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    bad = {r["id"] for r in rows if r.get("error")}
    have_clean = {r["id"] for r in rows if not r.get("error")}
    todo = [r for r in recs if r["id"] in bad or (r["id"] not in have_clean)]
    print(f"{backend} {name}: {len(recs)} in set, {len(bad)} errored ids, {len(todo)} to run", flush=True)
    dec = get_decider(backend)
    stop = threading.Event()
    new: dict[str, list[dict]] = {}
    err_left: list[str] = []

    def one(r):
        if stop.is_set():
            return
        try:
            res = dec.decide_multi(classify_questions({"masked_text": r["input"]}))
        except CredentialError as e:
            stop.set()
            err_left.append(str(e))
            return
        n = max(len(res), 1)
        out = []
        for k, d in res.items():
            row = {"id": r["id"], "name": k, "choice": d.choice, "confidence": round(d.confidence, 5), "latency_ms": round(d.latency_ms, 2)}
            if "probs" in d.raw:
                row["probs"] = d.raw["probs"]
            if "tokens_in" in d.raw:
                row.update(tokens_in=d.raw["tokens_in"] / n, tokens_out=d.raw["tokens_out"] / n, cost_usd=d.raw["cost_usd"] / n)
            if "error" in d.raw:
                row["error"] = d.raw["error"]
            out.append(row)
        new[r["id"]] = out

    with ThreadPoolExecutor(WORKERS[backend]) as ex:
        list(ex.map(one, todo))
    ok = {i: rs for i, rs in new.items() if not any(x.get("error") for x in rs)}
    still_bad = sorted(set(new) - set(ok))
    keep = [r for r in rows if r["id"] not in ok]
    merged = keep + [x for rs in ok.values() for x in rs]
    order = {r["id"]: i for i, r in enumerate(recs)}
    merged.sort(key=lambda r: (order.get(r["id"], 10**9), r["name"]))
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in merged), encoding="utf-8")
    print(json.dumps({"merged_ids": len(ok), "still_errored_ids": len(still_bad), "credential_abort": err_left[:1],
                      "usage": dec.usage()}), flush=True)
    return 2 if err_left else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
