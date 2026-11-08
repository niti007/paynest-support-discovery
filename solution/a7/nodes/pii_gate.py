"""Box 1: pii_gate. Masks every string of the incoming record, vaults the values, re-scans, fails closed.

Input state:  {"raw_record": <one record of any of the 5 sources>}  (transient)
Output state: ticket_id, vault_key, source, record (masked), masked_text, pii_placeholders,
              pii_gate_status ("passed" | "blocked"), trace. `raw_record` is never returned.
On "blocked" no record or text is returned at all.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from ..pii.masker import PlaceholderCounter, mask_record
from ..pii.scanner import scan_record
from ..state import TicketState
from ..vault import Vault


def identify(rec: dict[str, Any]) -> tuple[str, str, str]:
    """(source, ticket_id, vault_key). WhatsApp/calls key the vault by thread/call id to avoid cross-source clashes."""
    if "ticket_id" in rec and "channel" in rec:
        return "email", rec["ticket_id"], rec["ticket_id"]
    if "tkt" in rec:
        return "widget", rec["tkt"], rec["tkt"]
    if "reference" in rec:
        return "partner", rec["reference"], rec["reference"]
    if "thread_id" in rec:
        return "whatsapp", rec.get("ticket_id") or rec["thread_id"], rec["thread_id"]
    if "call_id" in rec:
        return "call", rec.get("ticket_id") or rec["call_id"], rec["call_id"]
    raise ValueError("unrecognised record schema")


def masked_text_of(source: str, rec: dict[str, Any]) -> str:
    if source == "email":
        return f'{rec["subject"]} {rec["body"]}'
    if source == "widget":
        return f'{rec["title"]} {rec["message"]}'
    if source == "partner":
        return f'{rec["summary"]} {rec["details"]}'
    if source == "whatsapp":
        return "\n".join(f'{m["from"]}: {m["text"]}' for m in rec["messages"])
    return rec["summary"]


def make_pii_gate(vault: Vault) -> Callable[[TicketState], TicketState]:
    """Factory: the vault is injected, no globals."""

    def pii_gate(state: TicketState) -> TicketState:
        raw = state["raw_record"]
        source, ticket_id, vkey = identify(raw)
        counter = PlaceholderCounter()
        masked, entries = mask_record(raw, vkey, counter)
        vault.put(vkey, entries)
        placeholders = {e.placeholder: e.pii_type for e in entries}

        residual = Counter(h.type for _, h in scan_record(masked))
        out: TicketState = {k: v for k, v in state.items() if k != "raw_record"}  # type: ignore[assignment]
        out.update(ticket_id=ticket_id, vault_key=vkey, source=source)  # type: ignore[typeddict-item]
        trace = list(state.get("trace", []))
        if residual:
            trace.append({"node": "pii_gate", "status": "blocked", "residual": dict(residual)})
            out.update(pii_gate_status="blocked", pii_placeholders=placeholders, trace=trace)
            return out
        trace.append({"node": "pii_gate", "status": "passed", "placeholders": dict(Counter(placeholders.values()))})
        out.update(record=masked, masked_text=masked_text_of(source, masked), pii_placeholders=placeholders,
                   pii_gate_status="passed", trace=trace)
        return out

    return pii_gate
