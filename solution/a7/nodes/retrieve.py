"""Box 8: retrieve. Hybrid search (pgvector + tsvector, RRF) scoped to the tenant (RLS), its region and the ticket date.

as_of = the ticket's received date (UTC date of received_at). search.VALID drops chunks that are not valid on that
date, so superseded KB chunks (e.g. KB-0201/0202 after 2026-07-15) never come back. The query is the masked ticket text
plus the English hint of the classified category (the tsvector is English; the embedding is multilingual).

STATUTORY tickets get one more step (why: a vague "PF figures differ from our CA's computation" ticket matched the superseded
KB chunk "PF: wage ceiling" on the old date, but after the rule change that chunk is filtered out, the short rule chunk ("EPF statutory
wage ceiling raised") shares no strong words with the ticket, and the query's "PF" is dropped from the tsquery as a 2-letter token;
the rule fell out of the top-5 for 5 of 30 PF tickets):
  1. TOPIC PROBE   the same search with the date filter off (as_of = 1970) ranks every KB chunk, valid or superseded; the topic of its
                   best hit is the ticket's topic (here pf_wage_ceiling).
  2. ADD RULES     every rule chunk in force at received_at for the ticket's region and that topic is added to the retrieved set
                   (after the vector/text hits, never instead of them).
  3. state keys    ticket_topic = the probed topic; topic_rules = ids of the in-force rules for (region, topic) at received_at.
                   output_guard uses topic_rules: a rule must be cited only when one is in force for the topic.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from .. import db, embed as embed_mod
from ..decide.base import CATEGORY_HINTS
from ..search import VALID, hybrid_search
from ..state import TicketState

K = 5
PROBE_AS_OF = date(1970, 1, 1)      # before any chunk's effective_to: every KB chunk is 'valid', rules (effective_from > 1970) are not
Retriever = Callable[[TicketState], list[dict[str, Any]]]


def _iso(d: Any) -> str | None:
    return d.isoformat() if isinstance(d, date) else (str(d) if d else None)


def _with_in_force_rules(conn: Any, state: TicketState, hits: list[dict[str, Any]], query: str, qe: Any, as_of: date) -> list[dict[str, Any]]:
    """Steps 1-3 of the module docstring. Writes ticket_topic / topic_rules into `state` (the retrieve node passes its own copy)."""
    region = state["tenant_region"]
    probe = hybrid_search(conn, query, region=region, as_of=PROBE_AS_OF, query_embedding=qe, k=1)
    topic = probe[0]["topic"] if probe else None
    rules = conn.execute(
        f"SELECT source, source_id, topic, region, effective_from, effective_to, superseded_by FROM kb_chunks "
        f"WHERE source = 'rule' AND topic = %(topic)s AND {VALID} ORDER BY source_id, chunk_index",
        {"topic": topic, "as_of": as_of, "region": region}).fetchall() if topic else []
    state["ticket_topic"] = topic
    state["topic_rules"] = list(dict.fromkeys(r[1] for r in rules))
    have = {h["source_id"] for h in hits}
    cols = ("source", "source_id", "topic", "region", "effective_from", "effective_to", "superseded_by")
    extra = [{**dict(zip(cols, r)), "score": 0.0} for r in rules if r[1] not in have]
    return hits + list({e["source_id"]: e for e in extra}.values())


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
                if cat == "statutory":
                    hits = _with_in_force_rules(conn, state, hits, query, qe, as_of)
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
