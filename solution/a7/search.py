"""Hybrid retrieval over kb_chunks: pgvector cosine + tsvector, fused with reciprocal rank fusion.

Filters (always applied): region in (region, 'ALL'); validity at `as_of`:
  (effective_from IS NULL OR effective_from <= as_of) AND (effective_to IS NULL OR effective_to >= as_of)
RLS additionally limits rows to global chunks plus the connection's tenant.
Pass query_embedding=None for the tsvector-only (offline) path.
"""
from __future__ import annotations

import re
from datetime import date

VALID = ("(effective_from IS NULL OR effective_from <= %(as_of)s) "
         "AND (effective_to IS NULL OR effective_to >= %(as_of)s) "
         "AND region IN (%(region)s, 'ALL')")


def _or_query(text: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", text) if len(w) > 2]
    return " | ".join(dict.fromkeys(w.lower() for w in words)) or "none"


def hybrid_search(conn, query: str, *, region: str, as_of: date,
                  query_embedding: list[float] | None = None, k: int = 5) -> list[dict]:
    p = {"as_of": as_of, "region": region, "q": _or_query(query), "k": k, "pool": max(k * 4, 20)}
    fts = (f"SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, to_tsquery('english', %(q)s)) DESC) AS r "
           f"FROM kb_chunks WHERE {VALID} AND tsv @@ to_tsquery('english', %(q)s) LIMIT %(pool)s")
    if query_embedding is not None:
        p["emb"] = "[" + ",".join(f"{x:.7f}" for x in query_embedding) + "]"
        vec = (f"SELECT id, row_number() OVER (ORDER BY embedding <=> %(emb)s::vector) AS r "
               f"FROM kb_chunks WHERE {VALID} AND embedding IS NOT NULL "
               f"ORDER BY embedding <=> %(emb)s::vector LIMIT %(pool)s")
        fused = f"WITH f AS ({fts}), v AS ({vec}), u AS (SELECT id, r FROM f UNION ALL SELECT id, r FROM v)"
    else:
        fused = f"WITH f AS ({fts}), u AS (SELECT id, r FROM f)"
    sql = (f"{fused} SELECT c.source, c.source_id, c.topic, c.region, c.effective_from, c.effective_to, "
           f"c.superseded_by, sum(1.0 / (60 + u.r)) AS score "
           f"FROM u JOIN kb_chunks c ON c.id = u.id GROUP BY c.id ORDER BY score DESC, c.source_id LIMIT %(k)s")
    cur = conn.execute(sql, p)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
