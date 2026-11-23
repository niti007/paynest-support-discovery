"""Lane split of the control-set PF tickets (pre-change PF questions, no rule in force yet) from the harvest, no Bedrock call.

Phase 5 A found that all 20 sampled control tickets ended in human_review via `statutory_no_rule_cited`. After p34-fixes3 the guard
requires a rule citation only when a rule is in force for the region/topic on the ticket date, so these tickets must be able to reach
draft_for_agent (a human still approves) and none may be held for that reason.
"""
from __future__ import annotations

import collections

import pytest

from solution.evals import results

pytestmark = pytest.mark.eval


def test_control_lane_split(harvest):
    ctl = [r for r in harvest if r["set"] == "control"]
    assert ctl, "no control tickets in the harvest"
    lanes = collections.Counter(r["lane"] for r in ctl)
    no_rule = [r["id"] for r in ctl if "statutory_no_rule_cited" in ((r.get("guard") or {}).get("failures") or [])]
    results.record("control_lanes", "Control-set PF tickets: lane split (before fixes3: 20 of 20 human_review via statutory_no_rule_cited)",
                   "none held for statutory_no_rule_cited", f"{dict(lanes)}; statutory_no_rule_cited on {len(no_rule)}", f"SAMPLE n={len(ctl)} of 29 control tickets (seed 20261108)",
                   not no_rule, "A human approves every draft_for_agent; human_review means the guard or the router held it for a person.")
    assert not no_rule, no_rule
