"""RAGAS 0.4.3 retrieval quality on the STALE and CONTROL sets (SAMPLES of the harvest) + the effective-date check.

- Context precision / context recall: RAGAS classic API (`ragas.metrics`: LLMContextPrecisionWithReference, LLMContextRecall), one import
  path in this module. Judge = Qwen3-235B through langchain-aws ChatBedrockConverse behind LangchainLLMWrapper. The judge sees MASKED
  ticket text and global KB/rule chunks only. RAGAS 0.4.3 needs the stub in ragas_compat.py (it imports a removed Vertex module).
- Reference answers come from the statutory rule table (R-UAE-WPS-2026-07, R-IN-PF-2026-08) and the ticket's received date (as_of):
  the correct answer on a date is the rule in force that day. Not hand-tuned on any model output.
- Effective-date check (plain code, all harvested tickets): every retrieved chunk must be valid on the ticket's received date
  (effective_from <= as_of, effective_to >= as_of): 0 stale chunks. This is the Session 4 "0 stale chunks" criterion.
"""
from __future__ import annotations

import asyncio
import os
import json
import statistics
from pathlib import Path

import pytest

import solution.evals.ragas_compat  # noqa: F401,E402  (must run before ragas is imported)
from ragas.dataset_schema import SingleTurnSample  # noqa: E402
from ragas.metrics import LLMContextPrecisionWithReference, LLMContextRecall  # noqa: E402  (classic path; ragas.metrics.collections is NOT used here)

from solution.evals import judges, results, spend  # noqa: E402
from solution.evals.refs import ctx_text, meta_by_id, reference_for  # noqa: E402

pytestmark = pytest.mark.eval
@pytest.fixture(scope="module")
def sample(harvest):
    m = meta_by_id()
    only = os.environ.get("P5_SETS", "stale,control").split(",")
    rows = [(r, m[r["id"]]) for r in harvest if r["id"] in m and r["set"] in only and r["contexts"]]
    assert len(rows) >= 15, "too few stale/control rows with retrieved contexts"
    for r, _ in rows:
        judges.assert_masked(r["masked_text"], *(c["body"] for c in r["contexts"]))
    return rows


def test_effective_date_no_stale_chunks(harvest):
    """0 stale chunks over every harvested ticket (all sets): retrieved chunks are valid on the ticket's received date."""
    stale, total, rules = [], 0, 0
    for r in harvest:
        for c in r["retrieved"]:
            total += 1
            rules += c.get("kind") == "rule"
            if (c.get("effective_from") and c["effective_from"] > r["as_of"]) or (c.get("effective_to") and c["effective_to"] < r["as_of"]):
                stale.append((r["id"], c["source_id"]))
    results.record("stale_chunks", "Stale chunks retrieved (effective date vs ticket received date)", "0",
                   f"{len(stale)} stale of {total} retrieved chunks ({rules} statutory rule chunks)", f"n={len(harvest)} tickets, all sets in the harvest",
                   not stale, "A chunk is stale when effective_to < received date or effective_from > received date." + (f" Offenders: {stale[:5]}" if stale else ""))
    assert not stale


def test_expected_rule_retrieved(sample):
    """Plain-code companion to context recall: the rule that decides the answer is among the retrieved ids."""
    hit = [r["id"] for r, m in sample if m["rule"] in {c["source_id"] for c in r["retrieved"]}]
    miss = [(r["id"], m["rule"]) for r, m in sample if m["rule"] not in {c["source_id"] for c in r["retrieved"]}]
    by = {}
    for r, m in sample:
        by.setdefault(r["set"], [0, 0])
        by[r["set"]][1] += 1
        by[r["set"]][0] += r["id"] in hit
    results.record("rule_retrieved", "Decisive statutory rule is among the top-5 retrieved chunks", "info",
                   f"{len(hit)}/{len(sample)} (" + ", ".join(f"{k} {a}/{b}" for k, (a, b) in sorted(by.items())) + ")",
                   f"n={len(sample)} (stale + control samples)", None,
                   "Misses are retrieval-recall gaps (e.g. PF rule not retrieved for some PF tickets), not stale chunks: " + ", ".join(f"{i}:{r}" for i, r in miss[:6]))


@pytest.mark.judge
def test_ragas_context_precision_recall(sample):
    spend.guard()
    llm = judges.ragas_llm()
    prec, rec = LLMContextPrecisionWithReference(llm=llm), LLMContextRecall(llm=llm)
    sem = asyncio.Semaphore(4)

    async def one(row, meta):
        s = SingleTurnSample(user_input=row["masked_text"], retrieved_contexts=[ctx_text(c) for c in row["contexts"]],
                             reference=reference_for(row, meta))
        async with sem:
            return row["id"], row["set"], await prec.single_turn_ascore(s), await rec.single_turn_ascore(s)

    async def main():
        out = await asyncio.gather(*(one(r, m) for r, m in sample), return_exceptions=True)
        return out

    out = asyncio.run(main())
    ok = [o for o in out if not isinstance(o, Exception)]
    err = [o for o in out if isinstance(o, Exception)]
    assert len(ok) >= len(sample) * 0.8, f"too many RAGAS errors: {[repr(e)[:120] for e in err[:3]]}"
    notes = []
    for name in ("stale", "control"):
        v = [(p, r) for _, s, p, r in ok if s == name]
        if not v:
            continue
        mp, mr = statistics.mean(p for p, _ in v), statistics.mean(r for _, r in v)
        notes.append(f"{name}: precision {mp:.3f}, recall {mr:.3f} (n={len(v)})")
        results.record(f"ragas_{name}", f"RAGAS context precision / recall, {name} set sample", "info (no Session 4 threshold)",
                       f"precision {mp:.3f}, recall {mr:.3f}", f"SAMPLE n={len(v)} of {sum(1 for _, s in [(r['id'], r['set']) for r, _ in sample] if s == name)} harvested {name} tickets (seed 20261108)",
                       None, f"RAGAS 0.4.3 classic metrics, judge Qwen3-235B, references from statutory_rules + received date. {len(err)} sample(s) errored and were dropped.")
    allp = [p for _, _, p, _ in ok]
    assert all(0.0 <= x <= 1.0 for x in allp if x == x), notes
