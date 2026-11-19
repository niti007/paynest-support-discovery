"""Box 3: resolve_tenant. Code, not the model, decides the tenant.

1. A structured tenant id on the ticket (customer_ref_how == "given") is used if the customer exists.
2. Otherwise the partner end_customer / WhatsApp customer_hint is matched EXACTLY (case/space-insensitive) against
   partner_customer_map. One hit -> that customer. Zero or several hits -> tenant_how="ambiguous" and no tenant.
   "Falcon" alone never matches; "Falcon Trading LLC" -> C-1004 only (never C-1050 Falcon Contracting LLC).
Everything downstream (tools, retrieval, RLS) takes tenant_id from state, set only here.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Protocol

from .. import db
from ..state import TicketState


def _key(name: str | None) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


class TenantLookup(Protocol):
    def partner(self, name: str) -> list[str]: ...
    def customer(self, customer_id: str) -> dict[str, Any] | None: ...


class DbTenantLookup:
    """partner_customer_map is outside RLS; customers is under RLS, so the tenant is set before reading it."""

    def __init__(self, conn_factory: Callable[[], Any] | None = None):
        self._factory = conn_factory or (lambda: db.connect(app=True, autocommit=True))

    def partner(self, name: str) -> list[str]:
        with self._factory() as conn:
            rows = conn.execute(
                "SELECT customer_id FROM partner_customer_map "
                "WHERE lower(regexp_replace(trim(partner_name), '\\s+', ' ', 'g')) = %s", (_key(name),)).fetchall()
        return sorted({r[0] for r in rows})

    def customer(self, customer_id: str) -> dict[str, Any] | None:
        with self._factory() as conn:
            with db.tenant(conn, customer_id):
                row = conn.execute("SELECT customer_id, name, tier, region FROM customers WHERE customer_id = %s",
                                   (customer_id,)).fetchone()
        return {"customer_id": row[0], "name": row[1], "tier": row[2], "region": row[3]} if row else None


def customer_name_of(record: dict[str, Any] | None) -> str | None:
    rec = record or {}
    return rec.get("end_customer") or rec.get("customer_hint")


def make_resolve_tenant(lookup: TenantLookup | None = None) -> Callable[[TicketState], TicketState]:
    lk = lookup or DbTenantLookup()

    def resolve_tenant(state: TicketState) -> TicketState:
        out: TicketState = dict(state)  # type: ignore[assignment]
        tid, how = None, "ambiguous"
        reason = "no_structured_tenant_and_no_exact_partner_match"
        if state.get("customer_ref") and state.get("customer_ref_how") == "given":
            if lk.customer(state["customer_ref"]):
                tid, how, reason = state["customer_ref"], "given", "structured_tenant_field"
            else:
                reason = "structured_tenant_unknown"
        else:
            name = customer_name_of(state.get("record"))
            hits = lk.partner(name) if name else []
            if len(hits) == 1:
                tid, how, reason = hits[0], "partner_map", "partner_map_exact_name"
            elif len(hits) > 1:
                reason = "partner_map_several_candidates"
        info = lk.customer(tid) if tid else None
        if tid and not info:
            tid, how, reason = None, "ambiguous", "mapped_customer_missing"
        name = customer_name_of(state.get("record"))
        out.update(tenant_resolution={"ambiguous": how == "ambiguous", "partner_name": name,
                                      "customer_id": tid, "resolved_by": how},
                   parent_ticket_id=state.get("parent_ticket_id"), linked_jira=state.get("linked_jira"),
                   privacy_concern=bool(state.get("privacy_concern", False)),
                   # defaults for the keys Builder C reads; output_guard overwrites guard when it runs
                   guard=state.get("guard") or {"passed": None, "failures": [], "retries": 0, "final": False,
                                                "stale_kb": [], "missing_kb": False},
                   tenant_id=tid, tenant_how=how, tenant_region=info["region"] if info else None,
                   tenant_tier=info["tier"] if info else None,
                   trace=list(state.get("trace", [])) + [{"node": "resolve_tenant", "tenant_how": how, "reason": reason}])
        return out

    return resolve_tenant
