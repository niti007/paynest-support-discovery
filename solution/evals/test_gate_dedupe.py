"""Gate: dedupe_cluster / incidents, Phase 4 numbers re-measured offline on the cached Cohere embeddings (W 2 h, T_inc 0.55, T_link 0.75).

Needs solution/evals/cache/ (gitignored): `uv run python solution/evals/dedupe_corpus.py --embed` (about USD 0.005, 1,353 masked tickets).
Replays the EVAL split of the Phase 4 sweep (Meridian 30 + 26 resubmits + routing sample B 625); no tuning happens here.
Disclosure carried from phase4_dedupe.md: the sweep grid was extended downward after the eval result was first seen, so these
eval numbers are not a blind test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from solution.evals import results

pytestmark = pytest.mark.eval
EV = Path(__file__).resolve().parent
CACHE = EV / "cache" / "dedupe_vecs.npy"


@pytest.fixture(scope="module")
def m():
    if not CACHE.exists():
        pytest.skip("embedding cache missing: uv run python solution/evals/dedupe_corpus.py --embed")
    sys.path.insert(0, str(EV))
    import dedupe_sweep as ds                                     # noqa: PLC0415
    from solution.a7.nodes.dedupe_cluster import Params          # noqa: PLC0415
    corpus, vecs = ds.load()
    sp = ds.splits(corpus)
    idx, nv = ds.sims_for(corpus, vecs)
    d = ds.replay(corpus, idx, nv, sp["eval"]["stream"], Params(t_link=0.75, t_inc=0.55, window_hours=2.0))
    return ds.metrics(corpus, d, sp["eval"]["scored"], sp["sets"])


def test_meridian_incident_opens_at_fifth_ticket(m):
    ok = m["meridian_first_flagged"] == "PN-101239" and m["meridian_opened_by_5th_ticket"]
    results.record("dedupe_meridian", "Dedupe: Meridian incident opens at the 5th similar ticket (PN-101239)", "5th ticket",
                   f"first flagged {m['meridian_first_flagged']} (rank {m['meridian_first_flagged_rank']}); {m['meridian_in_incident']}/{m['meridian_total']} in one incident",
                   f"n={m['meridian_total']} Meridian tickets (full incident set)", ok)
    assert ok


def test_no_false_incidents_on_routing_tickets(m):
    results.record("dedupe_false_incidents", "Dedupe: false incidents on ordinary routing tickets", "0",
                   f"{m['false_incident_tickets']} tickets / {m['false_incident_clusters']} clusters", f"n={m['routing_tickets']} routing tickets (eval split B)",
                   m["false_incident_tickets"] == 0)
    assert m["false_incident_tickets"] == 0


def test_resubmits_linked_to_right_parent(m):
    ok = m["resubmits_linked_correct"] / m["resubmits_total"] >= 0.9 and m["resubmits_in_incident"] == 0
    results.record("dedupe_resubmits", "Dedupe: resubmits linked to the correct parent", "info (Phase 4: 25/26)",
                   f"{m['resubmits_linked_correct']}/{m['resubmits_total']} correct, {m['resubmits_linked_wrong_parent']} wrong parent, {m['resubmits_in_incident']} pulled into an incident",
                   f"n={m['resubmits_total']} resubmits", ok,
                   f"False links on routing tickets: {m['false_links_routing']}/{m['routing_tickets']} (mostly identical template twins). Not a blind test (grid extended after eval was seen).")
    assert ok
