"""Box 11: human_approval. Pauses the graph with interrupt() until a reviewer decides.

`interrupt()` re-runs this whole node on resume, so everything before it is idempotent
(approvals upsert DO NOTHING, escalation timers ON CONFLICT DO NOTHING).

Lanes that never wait: block, route_queue, auto_answer (shadow) -> approval.status = "not_required".
Waiting lanes: draft_for_agent, human_review, incident.
Resume value: {"status": "approved"|"rejected"|"edited", "reviewer": str, "edited_draft": str (when edited)}.
Edited drafts are scanned for PII (regex layers) and refused if any is found: masked text only.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from langgraph.types import interrupt

from .. import escalation
from ..human_db import maybe_conn, scoped
from ..pii.scanner import scan

UTC = dt.timezone.utc
WAITING_LANES = {"draft_for_agent", "human_review", "incident"}
DECISIONS = {"approved", "rejected", "edited"}


def validate_decision(d: Any) -> dict:
    if not isinstance(d, dict):
        raise ValueError("decision must be an object")
    status, reviewer = d.get("status"), (d.get("reviewer") or "").strip()
    if status not in DECISIONS:
        raise ValueError(f"status must be one of {sorted(DECISIONS)}")
    if not reviewer:
        raise ValueError("reviewer required")
    edited = d.get("edited_draft")
    if status == "edited":
        if not isinstance(edited, str) or not edited.strip():
            raise ValueError("edited_draft required when status is edited")
        hits = scan(edited, presidio=False)          # regex + legacy layers; the audit scan runs the full set
        if hits:
            raise ValueError(f"edited_draft contains PII ({sorted({h.type for h in hits})}); mask it first")
    else:
        edited = None
    return {"status": status, "reviewer": reviewer, "edited_draft": edited}


def make_human_approval(conn_factory: Callable[[], Any] | None = None, clock: Callable[[], dt.datetime] | None = None):
    now = clock or (lambda: dt.datetime.now(UTC))

    def human_approval(state: dict) -> dict:
        lane, tenant, tid = state.get("lane"), state.get("tenant_id"), state.get("ticket_id")
        if not tenant or not tid:
            raise ValueError("human_approval needs tenant_id and ticket_id")
        with maybe_conn(conn_factory() if conn_factory else None) as conn:
            escalation.create_timers(conn, state)                    # idempotent, before interrupt
            if lane not in WAITING_LANES:
                return {"approval": {"status": "not_required", "reviewer": None, "decided_at": None, "edited_draft": None}}
            with scoped(conn, tenant):
                conn.execute(
                    "INSERT INTO approvals (ticket_id, tenant_id, lane, priority, category, draft_masked) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (ticket_id) DO NOTHING",
                    (tid, tenant, lane, state.get("priority"), state.get("category"), state.get("draft")))
            decision = interrupt({
                "ticket_id": tid, "lane": lane, "draft": state.get("draft"), "citations": state.get("citations"),
                "guard": state.get("guard"), "route_reasons": state.get("route_reasons")})
            d = validate_decision(decision)
            decided_at = now()
            with scoped(conn, tenant):
                row = conn.execute(
                    "UPDATE approvals SET status=%s, reviewer=%s, edited_draft_masked=%s, "
                    "decided_at = COALESCE(decided_at, %s) WHERE ticket_id=%s RETURNING decided_at",
                    (d["status"], d["reviewer"], d["edited_draft"], decided_at, tid)).fetchone()
            escalation.resolve_timers(conn, tenant, tid, decided_at)
        return {"approval": {"status": d["status"], "reviewer": d["reviewer"],
                             "decided_at": row[0].isoformat(), "edited_draft": d["edited_draft"]}}

    return human_approval


human_approval = make_human_approval()
