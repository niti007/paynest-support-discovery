"""LLM stale-claim check on Bedrock (Nova Lite, Qwen3-32B fallback). Marked aws: run with `pytest -m aws solution/tests/test_guard_llm.py`.

Covers (a) every wrong draft the code rules cannot flag (the h3 rule bypasses and the regression drafts listed in data/rules_gap.json), and
(b) genuine corrections that name the old value as history, which must NOT be flagged. Nova is quota-limited to 10 RPM: ~30 calls, a few minutes.
Full-set numbers (h3 64/55, h1 57/27, 41/11): solution/evals/guard_measure.py, reported in results_card_phase5.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from solution.a7.nodes import stale_llm
from solution.tests.p3_fakes import PF, RULE
from solution.tests.test_guard_h3 import CASES, KNOWN_RULE_BYPASS, state

pytestmark = pytest.mark.aws
GAP = json.loads((Path(__file__).parent / "data" / "rules_gap.json").read_text())
GENUINE_HISTORY = ["G069", "G075", "G087", "G094", "G099", "G071"]        # old value named as history, all correct: must pass
BY_ID = {c["id"]: c for c in CASES}


def flag(case: dict) -> stale_llm.StaleVerdict:
    v = stale_llm.check(state(case))
    assert v.error is None, v.error
    return v


@pytest.mark.parametrize("cid", sorted(KNOWN_RULE_BYPASS))
def test_llm_flags_the_h3_rule_bypasses(cid):
    v = flag(BY_ID[cid])
    assert v.flag, (cid, v.reason)
    assert v.backend and v.tokens_in > 0 and v.cost_usd > 0


@pytest.mark.parametrize("cid", GENUINE_HISTORY)
def test_llm_accepts_genuine_history_mentions(cid):
    v = flag(BY_ID[cid])
    assert not v.flag, (cid, v.reason)


def test_llm_flags_the_regression_drafts_the_rules_miss():
    rows = GAP["stale_values"] + GAP["h1_extra"]
    missed = []
    for x in rows:
        ch = RULE if x["rule"] == "wps" else PF
        st = {"masked_text": "Question about the rule.", "draft": x["draft"], "as_of": "2026-08-30" if x["rule"] == "pf" else "2026-07-20",
              "context_chunks": [ch], "category": "statutory"}
        v = stale_llm.check(st)
        assert v.error is None, v.error
        if not v.flag:
            missed.append(x["draft"])
    assert len(missed) <= len(rows) // 4, missed          # a model check, not a proof: at most a quarter may slip
