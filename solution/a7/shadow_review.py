"""Shadow-review: a reviewer grades a shadow auto_answer draft approved/rejected.

GRADES ONLY. Nothing is sent, queued or changed on the outbox row (it stays status='shadow'); the kill switch and
AUTO_SEND are untouched. On "approved" the masked question + draft go through cache.store_approved, so every never-cache
rule still applies (P1, statutory, tenant figures, PII, guard failed, not How-To, injection). A cache-served answer is
not re-stored. The decision, reviewer and cache outcome are recorded in shadow_reviews (one grade per ticket).

All functions expect a connection already scoped to one tenant (RLS) and inside a transaction.
"""
from __future__ import annotations

from typing import Any, Callable

from . import cache


class AlreadyReviewed(Exception):
    pass


class NotFound(Exception):
    pass


def list_pending(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT o.id, o.ticket_id, o.masked_body, o.created_at, o.payload FROM outbox o "
        "WHERE o.shadow AND o.kind = 'reply' AND o.status = 'shadow' AND NOT EXISTS "
        "(SELECT 1 FROM shadow_reviews r WHERE r.tenant_id = o.tenant_id AND r.ticket_id = o.ticket_id) "
        "ORDER BY o.created_at").fetchall()
    out = []
    for r in rows:
        r = r if isinstance(r, dict) else dict(zip(("id", "ticket_id", "masked_body", "created_at", "payload"), r))
        ctx = (r["payload"] or {}).get("cache_ctx") or {}
        out.append({"outbox_id": r["id"], "ticket_id": r["ticket_id"], "draft_masked": r["masked_body"],
                    "created_at": r["created_at"], "priority": ctx.get("priority"), "category": ctx.get("category")})
    return out


def review(conn, tenant_id: str, ticket_id: str, decision: str, reviewer: str,
           embedder: Callable[..., list[list[float]]] | None = None) -> dict[str, Any]:
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    if not (reviewer or "").strip():
        raise ValueError("reviewer required")
    reviewer = reviewer.strip()
    row = conn.execute("SELECT id, masked_body, payload FROM outbox WHERE ticket_id = %s AND shadow AND kind = 'reply' "
                       "AND status = 'shadow'", (ticket_id,)).fetchone()
    if not row:
        raise NotFound(ticket_id)
    oid, body, payload = (row["id"], row["masked_body"], row["payload"]) if isinstance(row, dict) else row
    ins = conn.execute("INSERT INTO shadow_reviews (tenant_id, ticket_id, outbox_id, decision, reviewer) VALUES (%s,%s,%s,%s,%s) "
                       "ON CONFLICT (tenant_id, ticket_id) DO NOTHING RETURNING id", (tenant_id, ticket_id, oid, decision, reviewer)).fetchone()
    if not ins:
        raise AlreadyReviewed(ticket_id)
    status, reason = "none", None
    if decision == "approved":
        ctx = (payload or {}).get("cache_ctx") or {}
        if ctx.get("cache_hit"):
            status, reason = "skipped", "served from cache, unchanged"
        elif not ctx.get("masked_text"):
            status, reason = "skipped", "no question context on the shadow row"
        else:
            state = {**ctx, "ticket_id": ticket_id, "tenant_id": tenant_id, "lane": "auto_answer", "draft": body,
                     "approval": {"status": "approved", "reviewer": reviewer}}
            try:
                emb = (embedder or cache._default_embedder)([state["masked_text"]], "search_query")[0]
                with conn.transaction():
                    stored, reason = cache.store_approved(conn, state, emb)
                status = "stored" if stored else "skipped"
            except Exception as e:  # noqa: BLE001
                status, reason = "error", type(e).__name__
    conn.execute("UPDATE shadow_reviews SET cache_status = %s, cache_reason = %s WHERE id = %s", (status, reason, ins["id"] if isinstance(ins, dict) else ins[0]))
    return {"ticket_id": ticket_id, "decision": decision, "reviewer": reviewer, "cache": status, "cache_reason": reason,
            "sent": False}
