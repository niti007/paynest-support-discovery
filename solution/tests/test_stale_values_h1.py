"""Held-out set H1 (Gate 3/4 re-validation 2) as regression tests: 57 wrong drafts must trip the stale-value check, 27 genuine
corrections must not. Plus extra phrasings of the same general patterns (history/contrast, number words, shorthand)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from solution.a7.nodes.output_guard import code_checks
from solution.tests.p3_fakes import PF, RULE
from solution.tests.test_p3_nodes import agent_state

CASES = json.loads((Path(__file__).parent / "data" / "h1_cases.json").read_text())


def flagged(draft: str) -> bool:
    st = agent_state(draft=draft, category="statutory", language="en", retrieved=[], context_chunks=[RULE, PF],
                     masked_text="Our WPS file and PF setup need checking. Which values apply?", as_of="2026-08-30")
    return any(f.startswith("numbers_not_grounded") for f in code_checks(st))


@pytest.mark.parametrize("case", CASES["wrong"], ids=[f"wrong{i}" for i in range(len(CASES["wrong"]))])
def test_h1_wrong_draft_is_caught(case):
    assert flagged(case["text"]), case["text"]


@pytest.mark.parametrize("case", CASES["genuine"], ids=[f"genuine{i}" for i in range(len(CASES["genuine"]))])
def test_h1_genuine_correction_passes(case):
    assert not flagged(case["text"]), case["text"]


def test_h1_counts():
    assert (len(CASES["wrong"]), len(CASES["genuine"])) == (57, 27)


# extra, own phrasings (not in H1): same general patterns, other wording
EXTRA_WRONG = [
    "The ceiling is twenty-five thousand... no wait, it is fifteen thousand rupees a month.",
    "PF is capped at 15 thousand rupees.",
    "The limit is 0.15 lakh per month.",
    "SIF has fourteen columns.",
    "Your file has 14 columns, which is correct. Version 16 is not yet required.",
    "Your file has 14 columns, which is right. The bank accepts it.",
    "Is it still 15,000? Yes, it is 15k.",
    "Is it still 15,000? Yes. The ceiling is 15,000.",
    "Previously the ceiling was 25,000. Now it is 15k.",
    "The ceiling used to be 25k, but it is 15k from August 2026.",
    "The ceiling is 15k, unchanged.",
    "Ceiling: ١٥٠٠٠ روبية شهرياً.",
    "الحد الأقصى خمسة عشر ألف روبية.",
    "सीमा पंद्रह हज़ार रुपये है।",
    "Ceiling 15 hazaar hi hai.",
]
EXTRA_GENUINE = [
    "The old ceiling was 15k; the ceiling is now 25k per month.",
    "Until July 2026 the ceiling was 15,000. From 1 August 2026 it is 25,000.",
    "The outdated 15 thousand limit has been replaced: the current ceiling is 25 thousand per month.",
    "Your payroll was set up with 15,000, which is outdated. The ceiling now is 25,000.",
    "पहले सीमा पंद्रह हज़ार थी, अब पच्चीस हज़ार है।",
    "الحد القديم كان خمسة عشر ألفاً، والحد الحالي هو 25,000 روبية.",
    "Pehle 15 hazaar tha, ab 25 hazaar hai.",
    "The ceiling went from fifteen thousand to twenty five thousand.",
    "The file now needs sixteen columns; fourteen is no longer accepted.",
]


@pytest.mark.parametrize("draft", EXTRA_WRONG, ids=[f"xw{i}" for i in range(len(EXTRA_WRONG))])
def test_extra_wrong(draft):
    assert flagged(draft), draft


@pytest.mark.parametrize("draft", EXTRA_GENUINE, ids=[f"xg{i}" for i in range(len(EXTRA_GENUINE))])
def test_extra_genuine(draft):
    assert not flagged(draft), draft
