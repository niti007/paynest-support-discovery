"""Box 8: retrieve. Hybrid search (pgvector + tsvector, RRF) scoped to the tenant (RLS), its region and the ticket date.

as_of = the ticket's received date (UTC date of received_at). search.VALID drops chunks that are not valid on that
date, so superseded KB chunks (e.g. KB-0201/0202 after 2026-07-15) never come back. The query is the masked ticket text
plus the English hint of the classified category (the tsvector is English; the embedding is multilingual).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from .. import db, embed as embed_mod
from ..decide.base import CATEGORY_HINTS
from ..search import VALID, hybrid_search
from ..state import TicketState

K = 5
Retriever = Callable[[TicketState], list[dict[str, Any]]]


def _iso(d: Any) -> str | None:
    return d.isoformat() if isinstance(d, date) else (str(d) if d else None)


def db_retriever(conn_factory: Callable[[], Any] | None = None, embedder: Callable | None = None, k: int = K) -> Retriever:
    factory = conn_factory or (lambda: db.connect(app=True, autocommit=True))
    emb = embedder or embed_mod.embed

    def retrieve_chunks(state: TicketState) -> list[dict[str, Any]]:
        as_of = date.fromisoformat(state["as_of"])
        cat = state.get("category") or state.get("suggested_category") or ""
        query = f'{state["masked_text"]} {CATEGORY_HINTS.get(cat, "")}'.strip()
        try:
            qe = emb([query], "search_query")[0]
        except Exception:  # noqa: BLE001 - embeddings down: fall back to keyword-only retrieval
            qe = None
        with factory() as conn:
            with db.tenant(conn, state["tenant_id"]):
                hits = hybrid_search(conn, query, region=state["tenant_region"], as_of=as_of, query_embedding=qe, k=k)
                ids = [h["source_id"] for h in hits]
                rows = conn.execute(
                    f"SELECT source_id, title, body, last_reviewed FROM kb_chunks WHERE source_id = ANY(%(ids)s) "
                    f"AND {VALID} ORDER BY source_id, chunk_index",
                    {"ids": ids, "as_of": as_of, "region": state["tenant_region"]}).fetchall()
        bodies: dict[str, dict[str, Any]] = {}
        for sid, title, body, reviewed in rows:
            b = bodies.setdefault(sid, {"title": title, "body": "", "last_reviewed": _iso(reviewed)})
            b["body"] = (b["body"] + "\n" + body).strip()
        return [{**h, **bodies.get(h["source_id"], {"title": None, "body": "", "last_reviewed": None})} for h in hits]

    return retrieve_chunks


def make_retrieve(retriever: Retriever | None = None) -> Callable[[TicketState], TicketState]:
    fetch = retriever or db_retriever()

    def retrieve(state: TicketState) -> TicketState:
        as_of = (state.get("received_at") or "")[:10]
        out: TicketState = dict(state)  # type: ignore[assignment]
        out["as_of"] = as_of
        hits = fetch(out)
        out["retrieved"] = [{"source_id": h["source_id"], "kind": h.get("source") or h.get("kind"),
                             "effective_from": _iso(h.get("effective_from")), "effective_to": _iso(h.get("effective_to")),
                             "score": round(float(h.get("score", 0.0)), 5)} for h in hits]
        out["context_chunks"] = [{"source_id": h["source_id"], "title": h.get("title"), "body": h.get("body", ""),
                                  "effective_from": _iso(h.get("effective_from"))} for h in hits]
        # answer_cache key (box 7): the topic of the best KB chunk (kb_chunks.topic, the same value the flush trigger uses)
        out["topic"] = next((h.get("topic") for h in hits if (h.get("source") or h.get("kind")) == "kb" and h.get("topic")), None)
        out["region"] = state.get("tenant_region")
        out["trace"] = list(state.get("trace", [])) + [{"node": "retrieve", "as_of": as_of, "n": len(hits),
                                                         "ids": [h["source_id"] for h in hits]}]
        return out

    return retrieve
