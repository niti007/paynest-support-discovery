"""Box 13: log_and_learn. Writes audit_log (masked text only) and proposes KB / partner-map updates.

Every string headed for audit_log is scanned with pii/scanner.py first; any hit raises AuditPIIError and nothing is
inserted. Idempotent: rows carry a dedupe_key, so a re-run adds nothing.

Inputs read from state (all optional except ids):
  guard: {"stale_kb": [source ids], "missing_kb": bool|str}   -> kb_suggestions (stale_kb / missing_kb)
  approval.status == "edited"                                 -> kb_suggestions reason reviewer_edit (masked edit)
  tenant_resolution: {"ambiguous": bool, "partner_name": str, "customer_id": str, "resolved_by": str}
                                                              -> partner_map_suggestions (a proposal for a person)
  audit: list of {"event": str, "text": str?, "signals": dict?} from earlier nodes (copied, scanned)
"""
from __future__ import annotations

import json
from typing import Any, Callable

from psycopg.types.json import Jsonb

from ..human_db import maybe_conn, scoped
from ..pii.scanner import scan


class AuditPIIError(RuntimeError):
    pass


def assert_clean(*texts: str | None) -> None:
    for t in texts:
        if t:
            hits = scan(t, ner=False)
            if hits:
                raise AuditPIIError(f"refusing to write PII to audit ({sorted({h.type for h in hits})})")


def _signals(state: dict) -> dict:
    ap = state.get("approval") or {}
    cites = [c if isinstance(c, str) else {k: c.get(k) for k in ("source_id", "id", "effective_from", "as_of") if k in c}
             for c in (state.get("citations") or [])]
    return {"lane": state.get("lane"), "priority": state.get("priority"), "category": state.get("category"),
            "language": state.get("language"), "route_reasons": state.get("route_reasons") or [], "citations": cites,
            "guard": state.get("guard"), "approval_status": ap.get("status"), "reviewer": ap.get("reviewer"),
            "tool_calls": [{"name": t.get("name"), "ok": t.get("ok")} if isinstance(t, dict) else str(t)
                           for t in state.get("tool_calls") or []],
            "actions": [{k: a.get(k) for k in ("type", "status", "outbox_id", "reason")} for a in state.get("actions") or []]}


def make_log_and_learn(conn_factory: Callable[[], Any] | None = None):
    def log_and_learn(state: dict) -> dict:
        tenant, tid = state.get("tenant_id"), state.get("ticket_id")
        if not tenant or not tid:
            raise ValueError("log_and_learn needs tenant_id and ticket_id")
        ap = state.get("approval") or {}
        text = (ap.get("edited_draft") if ap.get("status") == "edited" else state.get("draft")) or ""
        sig = _signals(state)
        extra = [e for e in state.get("audit") or [] if isinstance(e, dict)]
        guard = state.get("guard") if isinstance(state.get("guard"), dict) else {}
        res = state.get("tenant_resolution") if isinstance(state.get("tenant_resolution"), dict) else {}

        # verify EVERYTHING first; one hit aborts before any insert
        assert_clean(text, json.dumps(sig, default=str), *[f"{e.get('text', '')} {json.dumps(e.get('signals', {}), default=str)}" for e in extra])

        kb: list[tuple] = []
        for sid in guard.get("stale_kb") or []:
            kb.append((sid, "stale_kb", None))
        if guard.get("missing_kb"):
            kb.append((None, "missing_kb", None))
        if ap.get("status") == "edited" and ap.get("edited_draft"):
            cites = state.get("citations") or []
            first = cites[0] if cites else None
            sid = first if isinstance(first, str) else (first or {}).get("source_id") if first else None
            kb.append((sid, "reviewer_edit", ap["edited_draft"]))

        with maybe_conn(conn_factory() if conn_factory else None) as conn, scoped(conn, tenant):
            conn.execute(
                "INSERT INTO audit_log (tenant_id, ticket_id, event, masked_text, signals, node, dedupe_key) "
                "VALUES (%s,%s,'ticket_processed',%s,%s,'log_and_learn',%s) ON CONFLICT (dedupe_key) DO NOTHING",
                (tenant, tid, text, Jsonb(sig), f"{tid}:ticket_processed"))
            for i, e in enumerate(extra):
                conn.execute(
                    "INSERT INTO audit_log (tenant_id, ticket_id, event, masked_text, signals, node, dedupe_key) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dedupe_key) DO NOTHING",
                    (tenant, tid, str(e.get("event", "note")), e.get("text"), Jsonb(e.get("signals") or {}),
                     e.get("node"), f"{tid}:audit:{i}"))
            for sid, reason, stext in kb:
                conn.execute("INSERT INTO kb_suggestions (tenant_id, ticket_id, source_id, reason, suggested_text) "
                             "SELECT %s,%s,%s,%s,%s WHERE NOT EXISTS (SELECT 1 FROM kb_suggestions WHERE ticket_id=%s "
                             "AND reason=%s AND source_id IS NOT DISTINCT FROM %s)",
                             (tenant, tid, sid, reason, stext, tid, reason, sid))
            if res.get("ambiguous") and res.get("resolved_by") and res.get("partner_name") and res.get("customer_id"):
                conn.execute("INSERT INTO partner_map_suggestions (tenant_id, ticket_id, partner_name, customer_id, resolved_by) "
                             "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (partner_name, customer_id) DO NOTHING",
                             (tenant, tid, res["partner_name"], res["customer_id"], res["resolved_by"]))
        return {"audit": list(state.get("audit") or [])}   # unchanged: the audit rows live in the table

    return log_and_learn


log_and_learn = make_log_and_learn()
