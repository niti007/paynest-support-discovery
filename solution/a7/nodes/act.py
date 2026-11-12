"""Box 12: act. Per-lane allow-list; every effect is a row in `outbox`. NOTHING is sent by this code.

Allow-list (sourced: S4 p.8-9, S2 constraints, brief section 4):
  lane             needs approval  records (outbox kind)                      PII restore
  auto_answer      no (shadow)     reply, shadow=true while AUTO_SEND=false   never
  draft_for_agent  yes             draft for a human to send                  at send time only
  route_queue      no              assignment (queue, parent/Jira link)       never
  incident         yes             cluster (incidents row), notify L2, ONE jira_intent    never
  human_review     yes             nothing is sent; hold released             never
  block            refused         nothing (no action on ticket text)         never
Jira / refund / credit proposals in state["actions"] (status "proposed") are only ever recorded as intents and
need approval. Never automated: payroll/contract writes, tax or legal advice, attachments, acting on ticket text.

`vault.reveal(..., caller="act")` is called ONLY from `render_outbound`, over an a7_act connection, when a sender
renders a queued reply. Revealed values are returned to that caller and never stored, logged or audited.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from psycopg.types.json import Jsonb

from ..human_db import connect_act, maybe_conn, scoped
from ..pii.patterns import PLACEHOLDER_RE
from ..pii.scanner import scan
from ..vault import KmsKeyProvider, LocalKeyProvider, PostgresVaultStore, Vault

APPROVED = {"approved", "edited"}
NEVER_AUTOMATED = {"payroll_write", "contract_write", "tax_advice", "legal_advice", "attachment", "ticket_text_action"}
INTENTS = {"jira": "jira_intent", "refund": "refund_intent", "credit": "credit_intent"}   # approval required, intent only
ALLOW = {  # lane -> (needs_approval, restore_pii)
    "auto_answer": (False, False), "draft_for_agent": (True, True), "route_queue": (False, False),
    "incident": (True, False), "human_review": (True, False), "block": (None, False),
}


class ActRefused(RuntimeError):
    """act was invoked for something the allow-list does not permit."""


def auto_send(conn=None) -> bool:
    """Kill switch. True only when the feature_flags row auto_send_enabled is on AND the env does not force it off
    (A7_AUTO_SEND=false / A7_KILL_SWITCH=1). Env can never turn it on. No connection or any error = OFF (shadow only)."""
    from .. import flags
    return flags.auto_send_enabled(conn)


def _guard_ok(guard: Any) -> bool:
    return not (isinstance(guard, dict) and (guard.get("ok") is False or guard.get("passed") is False))


def _final_text(state: dict) -> str:
    ap = state.get("approval") or {}
    return (ap.get("edited_draft") if ap.get("status") == "edited" and ap.get("edited_draft") else state.get("draft")) or ""


def _check_masked(text: str) -> None:
    hits = scan(text, presidio=False)
    if hits:
        raise ActRefused(f"outbound text contains unmasked PII ({sorted({h.type for h in hits})})")


def _outbox(conn, state: dict, *, kind: str, key: str, body: str, status: str, channel: str | None = None,
            shadow: bool = False, restore_pii: bool = False, approved_by: str | None = None, payload: dict | None = None) -> int | None:
    _check_masked(body)
    row = conn.execute(
        "INSERT INTO outbox (tenant_id, ticket_id, channel, masked_body, status, kind, lane, shadow, restore_pii, "
        "approved_by, payload, idempotency_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (idempotency_key) DO NOTHING RETURNING id",
        (state["tenant_id"], state["ticket_id"], channel, body, status, kind, state.get("lane"), shadow, restore_pii,
         approved_by, Jsonb(payload or {}), f"{state['ticket_id']}:{key}")).fetchone()
    if row:
        return row[0]
    return conn.execute("SELECT id FROM outbox WHERE idempotency_key = %s", (f"{state['ticket_id']}:{key}",)).fetchone()[0]


def make_act(conn_factory: Callable[[], Any] | None = None, embedder: Callable[..., list[list[float]]] | None = None):
    def act(state: dict) -> dict:
        lane, tenant, tid = state.get("lane"), state.get("tenant_id"), state.get("ticket_id")
        if lane not in ALLOW:
            raise ActRefused(f"unknown lane {lane!r}")
        if not tenant or not tid:
            raise ActRefused("tenant_id and ticket_id required")
        needs_approval, restore = ALLOW[lane]
        ap = state.get("approval") or {}
        status = ap.get("status")
        prior = [a for a in (state.get("actions") or []) if a.get("status") != "proposed"]
        proposals = [a for a in (state.get("actions") or []) if a.get("status") == "proposed"]
        res: list[dict] = []

        if needs_approval is None:                                   # block
            return {"actions": prior + [{"type": "refused", "status": "refused", "reason": "block lane: no actions"}]}
        if needs_approval:
            if status == "rejected":
                return {"actions": prior + [{"type": "none", "status": "skipped", "reason": "rejected by reviewer"}]}
            if status not in APPROVED:
                raise ActRefused(f"lane {lane} requires an approved decision, got {status!r}")
        reviewer = ap.get("reviewer") if status in APPROVED else None

        with maybe_conn(conn_factory() if conn_factory else None) as conn, scoped(conn, tenant):
            body = _final_text(state)
            if lane == "auto_answer":
                p1 = str(state.get("priority", "")).upper() == "P1"
                if p1 or not _guard_ok(state.get("guard")):
                    res.append({"type": "refused", "status": "refused",
                                "reason": "P1 never auto-answered" if p1 else "output_guard did not pass"})
                else:
                    shadow = not auto_send(conn)
                    oid = _outbox(conn, state, kind="reply", key="reply", body=body, channel="auto_answer",
                                  status="shadow" if shadow else "queued", shadow=shadow)
                    res.append({"type": "reply", "status": "shadow" if shadow else "queued", "outbox_id": oid, "shadow": shadow})
            elif lane == "draft_for_agent":
                oid = _outbox(conn, state, kind="draft", key="draft", body=body, channel="agent", status="awaiting_agent_send",
                              restore_pii=restore, approved_by=reviewer, payload={"vault_key": state.get("vault_key") or tid})
                res.append({"type": "draft", "status": "awaiting_agent_send", "outbox_id": oid})
                res.append(_cache_approved(conn, state, embedder))
            elif lane == "route_queue":
                q = state.get("queue")
                payload = {k: state[k] for k in ("queue", "parent_ticket_id", "linked_jira") if state.get(k)}
                oid = _outbox(conn, state, kind="assignment", key="assignment", body=f"Assign {tid} to {q or 'queue'}",
                              channel=q, status="queued", payload=payload)
                res.append({"type": "assignment", "status": "queued", "outbox_id": oid, "queue": q})
            elif lane == "incident":
                inc = conn.execute("SELECT id FROM incidents WHERE tenant_id=%s AND topic IS NOT DISTINCT FROM %s AND status='open' "
                                   "ORDER BY id LIMIT 1", (tenant, state.get("category"))).fetchone()
                if inc:
                    conn.execute("UPDATE incidents SET ticket_ids = array_append(ticket_ids, %s) "
                                 "WHERE id=%s AND NOT (%s = ANY(ticket_ids))", (tid, inc[0], tid))
                    inc_id = inc[0]
                else:
                    inc_id = conn.execute("INSERT INTO incidents (tenant_id, topic, ticket_ids) VALUES (%s,%s,%s) RETURNING id",
                                          (tenant, state.get("category"), [tid])).fetchone()[0]
                res.append({"type": "cluster", "status": "recorded", "incident_id": inc_id})
                n = _outbox(conn, state, kind="notify", key=f"incident:{inc_id}:notify", channel="L2", status="queued",
                            body=f"Incident {inc_id} ({state.get('category')}): alert L2, ticket {tid} joined", approved_by=reviewer)
                res.append({"type": "notify", "status": "queued", "outbox_id": n})
                if not any(a.get("type") == "jira" for a in proposals):
                    proposals.append({"type": "jira", "status": "proposed", "summary": f"Incident {inc_id}: {state.get('category')}",
                                      "_key": f"jira:incident:{inc_id}"})
            # human_review: nothing is recorded; the person's decision is the outcome

            for a in proposals:                                       # Jira / refund / credit: intent rows only
                t = a.get("type")
                if t in NEVER_AUTOMATED or t not in INTENTS:
                    res.append({"type": t, "status": "refused",
                                "reason": "never automated" if t in NEVER_AUTOMATED else "not on the allow-list"})
                elif status not in APPROVED:
                    res.append({"type": t, "status": "refused", "reason": "requires reviewer approval"})
                else:
                    oid = _outbox(conn, state, kind=INTENTS[t], key=a.get("_key") or f"{t}:{a.get('summary', '')}",
                                  channel=t, status="awaiting_execution", approved_by=reviewer,
                                  body=str(a.get("summary") or f"{t} intent for {tid}"),
                                  payload={k: v for k, v in a.items() if k not in {"status", "_key"}})
                    res.append({"type": t, "status": "intent_recorded", "outbox_id": oid})
        return {"actions": prior + res}

    return act


def _cache_approved(conn, state: dict, embedder) -> dict:
    """Box 7 write side: a human-approved, masked, cacheable How-To draft goes into answer_cache (tenant-scoped).
    A cache failure never fails act; the reason is recorded in the action list (masked, no text)."""
    from .. import cache
    ap = state.get("approval") or {}
    if ap.get("status") not in APPROVED or not state.get("masked_text"):
        return {"type": "cache", "status": "skipped", "reason": "not approved or no question text"}
    try:
        pre = cache.never_cache_reason(priority=state.get("priority"), category=state.get("category"), lane=state.get("lane"),
                                       citations=state.get("citations"), route_reasons=state.get("route_reasons"),
                                       draft=_final_text(state), approval_status=ap.get("status"), guard=state.get("guard"))
        if pre:
            return {"type": "cache", "status": "skipped", "reason": pre}
        from ..cache import _default_embedder
        emb = (embedder or _default_embedder)([state["masked_text"]], "search_query")[0]
        with conn.transaction():                       # savepoint inside scoped(): a cache error must not poison act
            stored, reason = cache.store_approved(conn, state, emb)
        return {"type": "cache", "status": "stored" if stored else "skipped", "reason": reason}
    except Exception as e:  # noqa: BLE001
        return {"type": "cache", "status": "error", "reason": type(e).__name__}


act = make_act()


def make_vault(act_conn=None) -> Vault:
    """A Vault over the a7_act connection (the only login that can SELECT pii_vault)."""
    keys = KmsKeyProvider() if os.environ.get("A7_KMS_KEY_ID") else LocalKeyProvider()
    return Vault(PostgresVaultStore(act_conn or connect_act(), commit=False), keys)


def render_outbound(masked_body: str, vault_key: str, *, vault: Vault | None = None, allow: bool = True) -> str:
    """Send-time rendering: put real values back into a queued reply. Only for rows with restore_pii=true.
    Returns the text to the sender; it must not be logged, stored or audited."""
    if not allow:
        return masked_body
    own = vault is None
    v = vault or make_vault()
    try:
        return PLACEHOLDER_RE.sub(lambda m: v.reveal(vault_key, m.group(0), caller="act"), masked_body)
    finally:
        if own:
            v.store.conn.close()
