from datetime import date

import pytest

from solution.a7 import db
from solution.a7.embed import FakeEmbedder, embed
from solution.a7.search import hybrid_search

Q = "How do I fix the WPS SIF file rejected on upload to the bank for UAE payroll?"
AS_OF = date(2026, 8, 10)
STALE = {"KB-0201", "KB-0202"}


def check(rows):
    ids = [r["source_id"] for r in rows]
    assert "R-UAE-WPS-2026-07" in ids[:3], ids
    assert not STALE & set(ids), ids


@pytest.mark.db
def test_tsvector_only(owner):
    check(hybrid_search(owner, Q, region="UAE", as_of=AS_OF))


@pytest.mark.db
def test_stale_visible_before_rule(owner):
    ids = {r["source_id"] for r in hybrid_search(owner, Q, region="UAE", as_of=date(2026, 7, 1))}
    assert STALE & ids and "R-UAE-WPS-2026-07" not in ids


@pytest.mark.db
def test_fake_embeddings_rolled_back(owner):
    conn = db.connect()  # open transaction; embeddings are faked, then rolled back
    try:
        f = FakeEmbedder()
        rows = conn.execute("SELECT id, coalesce(title,'') || ' ' || body FROM kb_chunks").fetchall()
        for (i, _), v in zip(rows, f([r[1] for r in rows])):
            conn.execute("UPDATE kb_chunks SET embedding=%s::vector WHERE id=%s", ("[" + ",".join(map(str, v)) + "]", i))
        check(hybrid_search(conn, Q, region="UAE", as_of=AS_OF, query_embedding=f([Q])[0]))
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.db
@pytest.mark.aws
def test_real_embeddings(owner):
    n = owner.execute("SELECT count(*) FROM kb_chunks WHERE embedding IS NULL").fetchone()[0]
    assert n == 0, "run load_kb --embed first"
    rows = hybrid_search(owner, Q, region="UAE", as_of=AS_OF, query_embedding=embed([Q], "search_query")[0])
    check(rows)
