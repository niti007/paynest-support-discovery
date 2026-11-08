"""Graph state for the A7 pipeline (minimal; Phase 3 extends it).

Rules:
- Raw text exists only in the transient `raw_record` key handed to pii_gate. The gate removes it and
  never returns it, so nothing downstream (checkpoints, logs, cache, LLM) sees raw values.
- `record` is the MASKED record in the original source schema. `masked_text` is its free text joined.
- `pii_placeholders` maps placeholder -> type only (e.g. {"<IBAN_1>": "IBAN"}); values live in the vault.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

Source = Literal["email", "widget", "partner", "whatsapp", "call"]
Language = Literal["en", "hi", "ar", "hinglish"]


class TicketState(TypedDict, total=False):
    # input (transient, removed by pii_gate)
    raw_record: dict[str, Any]
    # identity
    ticket_id: str
    vault_key: str                  # key used in pii_vault (ticket_id, or thread/call id for WhatsApp/calls)
    source: Source
    # box 1: pii_gate
    record: dict[str, Any]          # masked record, original schema
    masked_text: str
    pii_placeholders: dict[str, str]
    pii_gate_status: Literal["passed", "blocked"]
    # box 2: normalise
    channel: Source
    received_at: str                # UTC ISO-8601, e.g. 2026-09-26T11:04:45Z
    received_tz: str                # original offset, e.g. +04:00
    received_tz_assumed: bool       # True when the source was naive and IST was assumed
    customer_ref: str | None        # customers.csv id, or None when unresolved
    customer_ref_how: Literal["given", "name_exact", "unresolved"]
    category_raw: str
    category: str                   # canon() of category_raw
    language: Language
    is_payroll_window: bool
    # observability (masked/ids/counts only)
    trace: list[dict[str, Any]]
