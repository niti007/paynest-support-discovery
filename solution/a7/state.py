"""Graph state for the A7 pipeline (Phase 3 extends it).

Rules:
- Raw text exists only in the transient `raw_record` key handed to pii_gate. The gate removes it and
  never returns it, so nothing downstream (checkpoints, logs, cache, LLM) sees raw values.
- `record` is the MASKED record in the original source schema. `masked_text` is its free text joined.
- `pii_placeholders` maps placeholder -> type only (e.g. {"<IBAN_1>": "IBAN"}); values live in the vault.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

Source = Literal["email", "widget", "partner", "whatsapp", "call"]
Language = Literal["en", "hi", "ar", "hinglish"]
Lane = Literal["auto_answer", "draft_for_agent", "route_queue", "incident", "human_review", "block"]


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
    # box 3: resolve_tenant
    tenant_id: str | None           # set ONLY by code (resolve_tenant); tools read it via InjectedState
    tenant_how: Literal["given", "partner_map", "ambiguous"]
    tenant_resolution: dict[str, Any]       # {ambiguous, partner_name, customer_id, resolved_by} (Builder C learns partner names)
    parent_ticket_id: str | None    # Phase 4 fills it
    linked_jira: str | None
    privacy_concern: bool
    tenant_region: str | None       # IN | UAE
    tenant_tier: str | None         # Enterprise | Business | Standard
    # box 4: classify
    category_confidence: float
    suggested_category: str | None  # the suggest_backend's (Nova) category, attached for a human
    urgency: str                    # urgent | high | normal
    priority: str                   # P1 | P2 | P3
    is_injection: bool
    # box 6: route
    lane: Lane
    queue: str | None
    classify_lane: Literal["auto", "human"]   # box 4 verdict from decide/policy
    route_reasons: list[str]
    incident_signal: bool           # set by Phase 4 clustering; box 6 only reads it
    # box 5: dedupe_cluster
    lane_hint: str | None           # "incident" when dedupe opened/joined an incident; route maps it to lane=incident
    incident_id: int | None
    dedupe: dict[str, Any]          # ids/counts only
    # box 8: retrieve
    as_of: str                      # YYYY-MM-DD, the ticket's received date
    retrieved: list[dict[str, Any]]         # {source_id, kind, effective_from, effective_to, score}
    context_chunks: list[dict[str, Any]]    # {source_id, title, body, effective_from}: global KB text, no PII
    topic: str | None               # KB topic of the best retrieved KB chunk (the answer_cache key)
    ticket_topic: str | None        # statutory tickets: topic of the best KB chunk ignoring validity dates (superseded chunks included)
    topic_rules: list[str]          # statutory tickets: rule ids IN FORCE at received_at for (tenant region, ticket_topic); [] = none yet
    region: str | None              # tenant region IN | UAE (the answer_cache key)
    # box 7: cache_lookup
    cache: dict[str, Any]           # {status: hit|miss|skipped, similarity?, entry_id?, reason?}
    cache_hit: bool
    cached_from: str | None
    # box 9: agent
    llm_tier: Literal["cheap", "hard"]
    draft: str
    citations: list[dict[str, Any]]         # {source_id, effective_from}
    tool_calls: Annotated[list[dict[str, Any]], operator.add]   # {name, args_masked, ok}; nodes return DELTAS
    tool_numbers: list[str]                 # numbers (<= 9 digits) seen in tool outputs, for the grounding check
    # box 10: output_guard
    guard: dict[str, Any]                   # {passed, failures, retries, final, stale_kb: [ids], missing_kb: bool}
    # box 11-13 (Builder C)
    approval: dict[str, Any]                # {status, reviewer, decided_at, edited_draft}
    actions: list[Any]
    audit: list[Any]
    # observability (masked/ids/counts only)
    trace: list[dict[str, Any]]
