"""DeepEval 4.3.3 quality gates on SAMPLES of the harvest (real graph drafts), judge = Qwen3-235B via the native AmazonBedrockModel
(Converse; probed OK, no custom wrapper needed). Judges see MASKED text only (judges.assert_masked on every input).

- Faithfulness (threshold 0.9): draft vs the retrieved chunks the agent was given.  Session 4: >= 0.9.
- Answer correctness (GEval): draft vs the reference answer in force on the received date (stale + control samples).
- Dated citation (GEval): a statutory answer cites the rule id AND its effective date and does not present a superseded value as current.
- Guard check: the known-bad stale drafts ("14 columns" WPS v2, "INR 15,000" PF ceiling after the change) MUST be caught by
  output_guard (code), and the faithfulness judge should score them low; a correct draft must pass the guard and score high.
DEEPEVAL_TELEMETRY_OPT_OUT=1 (conftest). No test-time tuning of thresholds or prompts.
"""
from __future__ import annotations

import json
import random
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from deepeval.metrics import FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams

from solution.a7.nodes.output_guard import code_checks
from solution.evals import judges, results, spend
from solution.evals.refs import EFFECTIVE, ctx_text, meta_by_id, reference_for

pytestmark = pytest.mark.eval
SEED = 20261108
N_FAITH = 40
SCORES = Path(__file__).resolve().parent / "reports" / "p5_deepeval_scores.json"


def _save(key: str, obj: dict) -> None:
    d = json.loads(SCORES.read_text()) if SCORES.exists() else {}
    d[key] = obj
    SCORES.write_text(json.dumps(d, indent=1))


def faith_metric(judge):
    return FaithfulnessMetric(threshold=0.9, model=judge, include_reason=False, async_mode=False)


def correctness_metric(judge):
    return GEval(name="Correctness", model=judge, threshold=0.5, async_mode=False,
                 criteria="Decide whether the ACTUAL_OUTPUT gives the same answer as the EXPECTED_OUTPUT for the customer's question: the same rule or "
                          "value in force on the ticket date, and not a superseded value presented as current. Extra helpful detail is fine; "
                          "a contradiction, a missing decisive fact, or a stale value presented as current is not.",
                 evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT])


def citation_metric(judge):
    return GEval(name="Dated citation", model=judge, threshold=0.5, async_mode=False,
                 criteria="For an answer that relies on a statutory rule: (1) it cites the rule id (R-... or KB-...) that appears in RETRIEVAL_CONTEXT, "
                          "(2) it states that rule's effective date, and (3) it does not present an older, superseded value as the current one. "
                          "Score 1 when all three hold, low when a date is missing or a superseded value is stated as current.",
                 evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.RETRIEVAL_CONTEXT])


def case(row: dict, draft: str | None = None, expected: str | None = None) -> LLMTestCase:
    d = draft if draft is not None else row["draft"]
    judges.assert_masked(row["masked_text"], d, *(c["body"] for c in row["contexts"]), expected or "")
    return LLMTestCase(input=row["masked_text"], actual_output=d, expected_output=expected,
                       retrieval_context=[ctx_text(c) for c in row["contexts"]])


def run_parallel(fn, items, workers=4):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


@pytest.fixture(scope="module")
def judge():
    return judges.primary_judge()


@pytest.mark.judge
def test_faithfulness(drafts, judge):
    spend.guard()
    pool = sorted(drafts, key=lambda r: r["id"])
    rows = pool if len(pool) <= N_FAITH else sorted(random.Random(SEED).sample(pool, N_FAITH), key=lambda r: r["id"])

    def one(r):
        m = faith_metric(judges.primary_judge())
        m.measure(case(r))
        return r["id"], r["set"], m.score

    out = run_parallel(one, rows)
    sc = [s for _, _, s in out if s is not None]
    mean, share = statistics.mean(sc), sum(s >= 0.9 for s in sc) / len(sc)
    low = sorted((i for i, _, s in out if s is not None and s < 0.9))
    _save("faithfulness", {i: s for i, _, s in out})
    results.record("faithfulness", "DeepEval Faithfulness (draft vs retrieved chunks), judge Qwen3-235B", ">= 0.9",
                   f"mean {mean:.3f}; {100 * share:.0f}% of drafts >= 0.9", f"SAMPLE n={len(sc)} of {len(pool)} drafts (seed {SEED}); sets: stale/control/smoke",
                   mean >= 0.9, f"Below 0.9: {len(low)} drafts ({', '.join(low[:6])}). Faithfulness is judged against retrieved chunks only (tool outputs and the ticket text are not in the context), "
                                "so figures taken from payroll tools can score as unfaithful.")
    assert mean >= 0.9, (mean, low)


@pytest.mark.judge
def test_answer_correctness_and_dated_citation(drafts):
    spend.guard()
    m = meta_by_id()
    rows = sorted((r for r in drafts if r["set"] in ("stale", "control") and r["id"] in m), key=lambda r: r["id"])
    if len(rows) > 40:
        rows = sorted(random.Random(SEED).sample(rows, 40), key=lambda r: r["id"])
    assert rows

    def one(r):
        jd = judges.primary_judge()
        ref = reference_for(r, m[r["id"]])
        c1, c2 = correctness_metric(jd), citation_metric(jd)
        c1.measure(case(r, expected=ref))
        c2.measure(case(r))
        return r["id"], r["set"], c1.score, c2.score

    out = run_parallel(one, rows)
    _save("correctness", {i: s for i, _, s, _ in out}); _save("dated_citation", {i: s for i, _, _, s in out})
    for name, ix in (("stale", "stale"), ("control", "control")):
        v = [(c, d) for _, s, c, d in out if s == ix]
        if v:
            results.record(f"correctness_{name}", f"DeepEval answer correctness (GEval vs reference), {name} set drafts", "info (no Session 4 threshold)",
                           f"mean {statistics.mean(c for c, _ in v):.3f}; {100 * sum(c >= 0.5 for c, _ in v) / len(v):.0f}% >= 0.5", f"SAMPLE n={len(v)} {name} drafts", None,
                           "Reference = the rule in force on the received date (statutory_rules).")
            results.record(f"dated_citation_{name}", f"DeepEval dated-citation G-Eval, {name} set drafts", "info (no Session 4 threshold)",
                           f"mean {statistics.mean(d for _, d in v):.3f}; {100 * sum(d >= 0.5 for _, d in v) / len(v):.0f}% >= 0.5", f"SAMPLE n={len(v)} {name} drafts", None,
                           "Complements the code check in output_guard (rule cited and date shown).")
    assert all(0.0 <= x <= 1.0 for _, _, c, d in out for x in (c, d))


# ---------- guard check: known-bad stale drafts ----------
BAD = {"WPS": ("R-UAE-WPS-2026-07", "Please generate the SIF using the v2 layout with exactly 14 columns, as described in KB-0202, and upload it via the bank portal.",
               "From 15 July 2026 the WPS SIF file uses the v3 layout with 16 columns (rule R-UAE-WPS-2026-07, effective 15 July 2026). Please regenerate the SIF in the v3 layout before uploading it to the bank."),
       "PF": ("R-IN-PF-2026-08", "The EPF statutory wage ceiling is INR 15,000 per month, as in KB-0142. PF is computed at 12% of basic up to that ceiling.",
              "From 1 August 2026 the EPF statutory wage ceiling is INR 25,000 per month (rule R-IN-PF-2026-08, effective 1 August 2026). PF is computed at 12% of basic up to that ceiling.")}


def _state(row: dict, draft: str) -> dict:
    return {"masked_text": row["masked_text"], "draft": draft, "category": "statutory", "language": "en", "as_of": row["as_of"],
            "retrieved": row["retrieved"], "tool_numbers": [], "citations": [],
            "context_chunks": [{"source_id": c["source_id"], "title": None, "body": c["body"], "effective_from": c["effective_from"]} for c in row["contexts"]]}


@pytest.fixture(scope="module")
def guard_rows(harvest):
    """One English harvested ticket per rule whose decisive rule chunk was retrieved and whose received date is after the change."""
    out = {}
    for r in sorted(harvest, key=lambda r: r["id"]):
        for kind, (rule, _, _) in BAD.items():
            if kind in out or r["language"] not in ("en",) or r["as_of"] < EFFECTIVE[rule]:
                continue
            if rule in {c["source_id"] for c in r["retrieved"]} and any(c["source_id"] == rule for c in r["contexts"]):
                out[kind] = r
    missing = set(BAD) - set(out)
    if missing:
        pytest.skip(f"no harvested ticket with the {sorted(missing)} rule retrieved after its effective date")
    return out


@pytest.mark.parametrize("kind", ["WPS", "PF"])
def test_guard_catches_stale_draft_and_passes_correct_one(guard_rows, kind):
    row = guard_rows[kind]
    _, bad, good = BAD[kind]
    bad_fail, good_fail = code_checks(_state(row, bad)), code_checks(_state(row, good))
    results.record(f"guard_{kind.lower()}", f"output_guard catches the stale {'14 columns' if kind == 'WPS' else 'INR 15,000'} draft", "caught (code check fails)",
                   f"stale draft: {bad_fail or 'NOT CAUGHT'}; correct draft: {good_fail or 'passes'}", f"n=1 stale + 1 correct draft on ticket {row['id']} ({row['as_of']})",
                   bool(bad_fail) and not good_fail, "Deterministic code checks (numbers grounded / cited and retrieved / rule + date shown); no model involved.")
    assert bad_fail, "stale draft was NOT caught by output_guard"
    assert not good_fail, f"correct draft wrongly rejected: {good_fail}"


@pytest.mark.judge
def test_faithfulness_judge_scores_stale_drafts_low(guard_rows):
    spend.guard()
    notes, ok = [], True
    for kind in ("WPS", "PF"):
        row = guard_rows[kind]
        _, bad, good = BAD[kind]
        sb, sg = (faith_metric(judges.primary_judge()), faith_metric(judges.primary_judge()))
        sb.measure(case(row, bad)); sg.measure(case(row, good))
        notes.append(f"{kind}: stale {sb.score:.2f} vs correct {sg.score:.2f}")
        ok = ok and sb.score < 0.5 and sg.score > sb.score
    results.record("faithfulness_stale", "Faithfulness judge scores the stale drafts low (and the correct ones higher)", "stale < 0.5 and < correct",
                   "; ".join(notes), "n=2 stale + 2 correct drafts", ok, "Qwen3-235B judge on masked text.")
    assert ok, notes
