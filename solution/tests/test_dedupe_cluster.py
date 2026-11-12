"""Phase 4A: dedupe_cluster. Pure decision tests need nothing; node tests need postgres (A7_DATABASE_URL etc.)."""
import math
from datetime import datetime, timedelta, timezone

import pytest

from solution.a7 import db
from solution.a7.nodes.dedupe_cluster import Cand, Params, decide, make_dedupe_cluster
from solution.db import bootstrap

UTC = timezone.utc
T0 = datetime(2026, 9, 25, 4, 35, tzinfo=UTC)
P = Params(t_link=0.85, t_inc=0.80, window_hours=2.0)


def c(i, minutes, sim, parent=None, inc=None):
    return Cand(f"T{i}", T0 + timedelta(minutes=minutes), sim, parent, inc)


def test_no_candidates_is_clean():
    d = decide(T0 + timedelta(hours=1), [], P)
    assert not d.incident and d.parent_ticket_id is None


def test_fifth_similar_ticket_opens_incident_fourth_does_not():
    four = [c(i, i * 3, 0.9) for i in range(3)]          # 3 earlier + this = 4
    assert not decide(T0 + timedelta(minutes=12), four, P).incident
    five = four + [c(3, 9, 0.9)]
    d = decide(T0 + timedelta(minutes=15), five, P)
    assert d.incident and d.incident_id is None and len(d.cluster_ids) == 4


def test_window_and_threshold_are_respected():
    old = [c(i, i, 0.9) for i in range(4)]
    assert not decide(T0 + timedelta(hours=5), old, P).incident          # outside W
    low = [c(i, i, 0.7) for i in range(4)]
    assert not decide(T0 + timedelta(minutes=10), low, P).incident       # below T_inc


def test_joins_existing_incident_below_count():
    d = decide(T0 + timedelta(minutes=30), [c(1, 5, 0.88, inc=7)], P)
    assert d.incident and d.incident_id == 7


def test_resubmit_link_picks_best_parent_within_two_days():
    cands = [c(1, -60 * 30, 0.86), c(2, -60 * 40, 0.95), c(3, -60 * 60, 0.99)]   # 30h, 40h, 60h ago
    d = decide(T0, cands, P)
    assert d.parent_ticket_id == "T2" and not d.incident                  # T3 is >2 days old


def test_link_below_threshold_is_not_a_link():
    assert decide(T0, [c(1, -120, 0.84)], P).parent_ticket_id is None


def test_early_linked_tickets_still_count_toward_incident():
    cands = [c(i, i, 0.9, parent="X") for i in range(4)]
    assert decide(T0 + timedelta(minutes=10), cands, P).incident


def test_incident_wins_over_link():
    cands = [c(i, i, 0.95) for i in range(4)]
    d = decide(T0 + timedelta(minutes=10), cands, P)
    assert d.incident and d.parent_ticket_id is None


# ---------------------------------------------------------------- node (postgres)
def unit(k, dims=1024):
    v = [0.0] * dims
    v[k] = 1.0
    return v


def near(k, eps, dims=1024):
    v = unit(k)
    v[(k + 1) % dims] = eps
    n = math.sqrt(1 + eps * eps)
    return [x / n for x in v]


class VecEmbedder:
    def __init__(self, table):
        self.table = table

    def __call__(self, texts, input_type="search_document"):
        return [self.table[t] for t in texts]


@pytest.fixture()
def pg(owner):
    bootstrap.bootstrap()
    for cid in ("T-D1", "T-D2"):
        owner.execute("INSERT INTO customers VALUES (%s,%s,'Standard','IN',1,1) ON CONFLICT DO NOTHING", (cid, f"Test {cid}"))
    owner.execute("TRUNCATE ticket_embeddings, incidents")
    yield owner
    owner.execute("TRUNCATE ticket_embeddings, incidents")
    owner.execute("DELETE FROM customers WHERE customer_id LIKE 'T-D%'")


def state(tid, tenant, minutes, text, cat="payroll"):
    return {"ticket_id": tid, "tenant_id": tenant, "masked_text": text, "category": cat,
            "received_at": (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")}


@pytest.mark.db
def test_node_opens_incident_on_fifth_and_is_tenant_scoped(pg):
    emb = VecEmbedder({f"a{i}": near(0, 0.05 * i) for i in range(8)} | {"other": unit(500)})
    node = make_dedupe_cluster(emb)
    outs = [node(state(f"D-{i}", "T-D1", 3 * i, f"a{i}")) for i in range(5)]
    assert [o["lane_hint"] for o in outs] == [None, None, None, None, "incident"]
    assert outs[4]["incident_id"] and outs[4]["dedupe"]["cluster_size"] == 5
    row = pg.execute("SELECT tenant_id, ticket_ids, status FROM incidents").fetchall()
    assert len(row) == 1 and row[0][0] == "T-D1" and sorted(row[0][1]) == [f"D-{i}" for i in range(5)]
    sixth = node(state("D-5", "T-D1", 20, "a5"))                          # joins the open incident
    assert sixth["lane_hint"] == "incident" and sixth["incident_id"] == outs[4]["incident_id"]
    assert pg.execute("SELECT count(*) FROM incidents").fetchone()[0] == 1
    # another tenant with the same vectors sees nothing of T-D1
    o = node(state("E-0", "T-D2", 21, "a0"))
    assert o["lane_hint"] is None and o["parent_ticket_id"] is None


@pytest.mark.db
def test_node_links_resubmit_and_is_idempotent(pg):
    emb = VecEmbedder({"orig": unit(3), "follow": near(3, 0.2), "unrelated": unit(40)})
    node = make_dedupe_cluster(emb)
    assert node(state("R-1", "T-D1", 0, "orig"))["parent_ticket_id"] is None
    assert node(state("R-2", "T-D1", 60 * 20, "unrelated"))["parent_ticket_id"] is None
    out = node(state("R-3", "T-D1", 60 * 24, "follow"))
    assert out["parent_ticket_id"] == "R-1" and out["lane_hint"] is None
    out2 = node(state("R-3", "T-D1", 60 * 24, "follow"))                  # re-run: same answer, one row
    assert out2["parent_ticket_id"] == "R-1"
    assert pg.execute("SELECT count(*) FROM ticket_embeddings WHERE ticket_id='R-3'").fetchone()[0] == 1


@pytest.mark.db
def test_node_skips_without_tenant_and_stores_no_text(pg):
    node = make_dedupe_cluster(VecEmbedder({"x": unit(1)}))
    out = node({"ticket_id": "N-1", "masked_text": "x", "received_at": "2026-09-25T04:35:00Z"})
    assert out["lane_hint"] is None and out["dedupe"] == {"skipped": "no tenant"}
    cols = [r[0] for r in pg.execute("SELECT column_name FROM information_schema.columns WHERE table_name='ticket_embeddings'")]
    assert not {"text", "masked_text", "body"} & set(cols)


# ---------------------------------------------------------------- gate 4 on real (cached) embeddings
@pytest.mark.db
def test_gate4_meridian_opens_by_fifth_ticket(pg):
    import json
    from pathlib import Path

    import numpy as np
    cache = Path(__file__).resolve().parents[1] / "evals" / "cache"
    if not (cache / "dedupe_vecs.npy").exists():
        pytest.skip("embedding cache missing: uv run python solution/evals/dedupe_corpus.py --embed")
    corpus, vecs = json.loads((cache / "dedupe_corpus.json").read_text()), np.load(cache / "dedupe_vecs.npy")
    by_text = {r["text"]: vecs[i].tolist() for i, r in enumerate(corpus)}
    pg.execute("INSERT INTO customers VALUES ('C-1002','Test Meridian','Standard','IN',1,1) ON CONFLICT DO NOTHING")
    node = make_dedupe_cluster(VecEmbedder(by_text))
    mer = [r for r in corpus if r["tenant"] == "C-1002" and r["received_at"].startswith("2026-09-25")]
    flagged = [r["id"] for r in mer if node({"ticket_id": r["id"], "tenant_id": "C-1002", "masked_text": r["text"],
                                              "received_at": r["received_at"], "category": "payroll"})["lane_hint"] == "incident"]
    inc_set = [r["id"] for r in mer if r["incident"]]
    assert flagged[0] == inc_set[4] == "PN-101239"
    assert pg.execute("SELECT count(*) FROM incidents WHERE tenant_id='C-1002'").fetchone()[0] == 1
