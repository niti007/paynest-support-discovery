"""bakeoff.py must keep emitting the hand-added routing-policy section and the Gate 3 exceptions when it regenerates bakeoff.md."""
from solution.evals import bakeoff
from solution.evals.bakeoff import REPORTS, SETS_DIR


def test_regenerated_bakeoff_keeps_routing_section_and_gate3_exceptions():
    extra = bakeoff.routing_policy_markdown(REPORTS, SETS_DIR) + bakeoff.GATE3_EXCEPTIONS
    txt = "\n".join(extra)
    assert "## Routing policy change (user decision 2026-11-12)" in txt and "99.7% (299/300)" in txt and "10 of 60" in txt
    assert "## Gate 3 exceptions (user-accepted 2026-11-12)" in txt
    for needle in ("PN-100402", "0 of 2", "2 of 10", "PN-101333", "Payroll-Ops", "20261108", "2 of 20", "AUTO_SEND=false", "kill switch"):
        assert needle in txt, needle
    committed = (REPORTS / "bakeoff.md").read_text()
    assert "## Routing policy change (user decision 2026-11-12)" in committed and "## Gate 3 exceptions (user-accepted 2026-11-12)" in committed
