"""Second-judge cross-check for the DeepEval faithfulness score (replaces the blocked Bedrock Evaluations job).

    primary judge   Qwen3-235B   qwen.qwen3-235b-a22b-2507-v1:0
    cross-check     Nova Lite    eu.amazon.nova-lite-v1:0
    both through DeepEval's native AmazonBedrockModel (Converse); if a model does not work through it (probe), a langchain-aws
    ChatBedrockConverse wrapped as a DeepEvalBaseLLM is used instead.

SAMPLE: n drafts (default 24, fixed seed 20261108) from the stale + control + smoke sets, produced by the REAL graph; the judges see
MASKED text only (ticket text with placeholders, KB chunk text, the masked draft). Reports agreement and items differing > 0.2.

    uv run python -m solution.evals.judge_crosscheck --n 24            (AWS_PROFILE=paynest, compose up)
    uv run python -m solution.evals.judge_crosscheck --rescore         (reuse the cached drafts, judge again)
Spend: every call is added to the ledger (solution/evals/spend.py, cap USD 1.00).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
ROOT = Path(__file__).resolve().parents[2]
SETS = ROOT / "solution/evals/sets"
REPORTS = ROOT / "solution/evals/reports"
DRAFTS = REPORTS / "judge_sample_drafts.jsonl"
OUT = REPORTS / "judge_crosscheck.json"
SEED = 20261108
PRIMARY = "qwen.qwen3-235b-a22b-2507-v1:0"
CROSS = "eu.amazon.nova-lite-v1:0"
REGION = "eu-north-1"
DIFF = 0.2
THRESHOLD = 0.9      # the Session 4 faithfulness gate


def _jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def sample_inputs(n: int) -> list[dict]:
    """Seeded sample over stale + control + smoke rows (smoke rows are all kept, they carry the demo tickets)."""
    rows = []
    for name in ("stale", "control", "smoke"):
        rows += [{**r, "_set": name} for r in _jl(SETS / f"{name}.jsonl")]
    seen, uniq = set(), []
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["id"] not in seen:
            seen.add(r["id"]); uniq.append(r)
    return random.Random(SEED).sample(uniq, min(n, len(uniq)))


# ----------------------------------------------------------------------------------------------- drafts (real graph)
def make_drafts(n: int) -> list[dict]:
    from solution.evals import spend
    from solution.evals.promptfoo import provider as P      # the same real-graph runner the red team uses
    P._init()
    out = []
    for r in sample_inputs(n):
        spend.check(0.02)
        tenant = (r.get("meta") or {}).get("customer_ref") or "C-1004"
        created = ((r.get("meta") or {}).get("reply_at") or "2026-09-12T16:41:07")[:19].replace("T", " ")
        try:
            v = P.run_attack(r["input"], "JC-" + r["id"], tenant=tenant, created=created, with_context=True)
        except Exception as e:  # noqa: BLE001
            print("skip", r["id"], type(e).__name__, flush=True); continue
        if not v.get("draft") or not v.get("context"):
            print("no draft/context", r["id"], v.get("lane"), flush=True); continue
        out.append({"id": r["id"], "set": r["_set"], "lang": r.get("language"), "lane": v["lane"], "input": r["input"], "draft": v["draft"],
                    "context": v["context"], "cost_usd": v["cost_usd"]})
        print("draft", r["id"], v["lane"], flush=True)
    DRAFTS.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in out) + "\n")
    return out


# ----------------------------------------------------------------------------------------------- judges
def langchain_judge(model_id: str):
    """Fallback: langchain-aws ChatBedrockConverse as a DeepEvalBaseLLM (supports schema= for structured output)."""
    from deepeval.models import DeepEvalBaseLLM
    from langchain_aws import ChatBedrockConverse

    class LCJudge(DeepEvalBaseLLM):
        def __init__(self):
            self.m = ChatBedrockConverse(model=model_id, region_name=REGION, temperature=0, max_tokens=2048)
            self.name = model_id
            self.tokens = [0, 0]

        def load_model(self):
            return self.m

        def _track(self, r):
            u = getattr(r, "usage_metadata", None) or {}
            self.tokens[0] += u.get("input_tokens", 0); self.tokens[1] += u.get("output_tokens", 0)

        def generate(self, prompt: str, schema=None):
            r = self.m.invoke(prompt); self._track(r)
            text = r.content if isinstance(r.content, str) else "".join(b.get("text", "") for b in r.content if isinstance(b, dict))
            if schema is None:
                return text
            return schema(**_json_of(text))

        async def a_generate(self, prompt: str, schema=None):
            return self.generate(prompt, schema)

        def get_model_name(self):
            return model_id

    return LCJudge()


def _json_of(text: str) -> dict:
    import re
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", t, flags=re.S)
    return json.loads(m.group(0))


def native_judge(model_id: str):
    from deepeval.models import AmazonBedrockModel
    from solution.evals import spend
    pin, pout = spend.PRICES.get(model_id, (0.22, 0.88))
    return AmazonBedrockModel(model=model_id, region=REGION, generation_kwargs={"temperature": 0, "maxTokens": 2048},
                              cost_per_input_token=pin / 1e6, cost_per_output_token=pout / 1e6)


def probe(judge) -> bool:
    """One tiny structured call through a real metric; True when the judge returns usable verdict JSON."""
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase
    tc = LLMTestCase(input="What colour is the sky?", actual_output="The sky is blue.", retrieval_context=["The sky is blue on a clear day."])
    m = FaithfulnessMetric(model=judge, threshold=THRESHOLD, include_reason=False, async_mode=False)
    s = m.measure(tc)
    return isinstance(s, (int, float))


def get_judge(model_id: str):
    try:
        j = native_judge(model_id)
        if probe(j):
            return j, "AmazonBedrockModel (native Converse)"
    except Exception as e:  # noqa: BLE001
        print(f"native AmazonBedrockModel failed for {model_id}: {type(e).__name__}: {str(e)[:160]}", flush=True)
    j = langchain_judge(model_id)
    if probe(j):
        return j, "langchain-aws ChatBedrockConverse as DeepEvalBaseLLM (fallback)"
    raise RuntimeError(f"no working judge for {model_id}")


def score(judge, d: dict) -> tuple[float | None, float]:
    from deepeval.metrics import FaithfulnessMetric
    from deepeval.test_case import LLMTestCase
    m = FaithfulnessMetric(model=judge, threshold=THRESHOLD, include_reason=False, async_mode=False)
    try:
        s = m.measure(LLMTestCase(input=d["input"], actual_output=d["draft"], retrieval_context=d["context"]))
    except Exception as e:  # noqa: BLE001
        print("judge error", d["id"], type(e).__name__, str(e)[:100], flush=True)
        return None, 0.0
    cost = m.evaluation_cost or 0.0
    if not cost and hasattr(judge, "tokens"):
        cost = 0.0
    return float(s), float(cost)


def run(drafts: list[dict]) -> dict:
    from solution.evals import spend
    pj, pmode = get_judge(PRIMARY)
    cj, cmode = get_judge(CROSS)
    rows = []
    for d in drafts:
        spend.check(0.03)
        a, ca = score(pj, d); b, cb = score(cj, d)
        spend.add("judge_primary_qwen235b", ca, id=d["id"]); spend.add("judge_cross_nova_lite", cb, id=d["id"])
        rows.append({"id": d["id"], "set": d["set"], "lang": d["lang"], "lane": d["lane"], "primary": a, "cross": b})
        print(d["id"], a, b, flush=True)
    return summarise(rows, pmode, cmode)


def summarise(rows: list[dict], pmode: str, cmode: str) -> dict:
    ok = [r for r in rows if r["primary"] is not None and r["cross"] is not None]
    diffs = [abs(r["primary"] - r["cross"]) for r in ok]
    over = [r for r in ok if abs(r["primary"] - r["cross"]) > DIFF]
    verdict = lambda s: s >= THRESHOLD
    both_pass = sum(1 for r in ok if verdict(r["primary"]) == verdict(r["cross"]))
    corr = None
    if len(ok) > 2 and len({r["primary"] for r in ok}) > 1 and len({r["cross"] for r in ok}) > 1:
        corr = round(statistics.correlation([r["primary"] for r in ok], [r["cross"] for r in ok]), 3)
    return {"n": len(rows), "n_scored_by_both": len(ok), "seed": SEED, "primary": PRIMARY, "cross": CROSS, "primary_mode": pmode, "cross_mode": cmode,
            "mean_primary": round(statistics.mean(r["primary"] for r in ok), 3) if ok else None,
            "mean_cross": round(statistics.mean(r["cross"] for r in ok), 3) if ok else None,
            "mean_abs_diff": round(statistics.mean(diffs), 3) if diffs else None,
            f"agree_within_{DIFF}": len(ok) - len(over), f"differ_over_{DIFF}": [{k: r[k] for k in ("id", "set", "lang", "primary", "cross")} for r in over],
            f"pass_fail_agreement_at_{THRESHOLD}": f"{both_pass}/{len(ok)}", "pearson": corr, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=24); ap.add_argument("--rescore", action="store_true")
    a = ap.parse_args()
    drafts = _jl(DRAFTS) if a.rescore and DRAFTS.exists() else make_drafts(a.n)
    res = run(drafts)
    OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
