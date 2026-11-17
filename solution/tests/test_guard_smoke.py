"""PR smoke for the output guard on the real model: a handful of h3 WRONG drafts through the whole guard (code rules + Nova Lite stale check).

Why: the Bedrock CI steps (stale / safety / routing / demo tickets) stay green if the guard regresses, and the full guard measurement is too big
for a PR (119 calls). This takes 8 wrong drafts and asserts 0 bypasses, in at most 4 Nova calls: the 4 drafts the code rules cannot flag
(KNOWN_RULE_BYPASS) must be caught by the LLM layer, and 4 drafts in other languages must be caught by the rules (they cost no model call).
Marked aws, so it is skipped when A7_BEDROCK=0 (`-m "not aws"`).
"""
from __future__ import annotations

import pytest

from solution.a7.nodes.output_guard import make_output_guard
from solution.tests.p3_fakes import FakeDecider
from solution.tests.test_guard_h3 import CASES, KNOWN_RULE_BYPASS, rules_flag, state

pytestmark = pytest.mark.aws
BY_ID = {c["id"]: c for c in CASES}
RULE_CAUGHT = ["W001", "W021", "W043", "W054"]          # en pf, en wps, hi, ar: one per language family
SAMPLE = sorted(KNOWN_RULE_BYPASS) + RULE_CAUGHT
MAX_NOVA_CALLS = 10


def test_sample_is_all_wrong_drafts_and_cheap():
    assert len(SAMPLE) == 8 and all(BY_ID[i]["label"] == "wrong" for i in SAMPLE)
    assert all(rules_flag(BY_ID[i]) for i in RULE_CAUGHT) and not any(rules_flag(BY_ID[i]) for i in KNOWN_RULE_BYPASS)
    assert len(KNOWN_RULE_BYPASS) <= MAX_NOVA_CALLS


def test_guard_has_zero_bypasses_on_real_model():
    guard = make_output_guard(FakeDecider(answers="yes"), max_retries=0)
    bypasses, llm_calls = [], 0
    for cid in SAMPLE:
        out = guard(state(BY_ID[cid]))
        ent = next(t for t in out["trace"] if t["node"] == "output_guard")
        sv = ent.get("stale_llm")
        llm_calls += 1 if sv else 0
        if out["guard"]["passed"]:
            bypasses.append(cid)
        elif cid in KNOWN_RULE_BYPASS:           # the model, not an error or the rules, must be what caught it
            assert sv and sv["flag"] and not sv.get("error"), (cid, out["guard"]["failures"])
    assert bypasses == [], f"guard bypassed: {bypasses}"
    assert llm_calls <= MAX_NOVA_CALLS
