"""Box 6: route. Plain-code rules, first match wins, every decision logs its reasons.

Precedence (Opus to confirm, the sources give none):
 1. injection flag                  -> block (+ human review attached; no draft, no tools)
 2. tenant ambiguous                -> human_review (no tenant, so no retrieval or tools)
 3. incident signal (Phase 4 sets)  -> incident
 4. classify sent it to a human     -> human_review (low confidence / invalid; Nova's suggestion is attached)
 5. statutory                       -> draft_for_agent (a human sends; never auto)
 6. owned-queue categories          -> route_queue (SOP section 1 queue table)
 7. P1 on a KB-answerable category  -> draft_for_agent; otherwise auto_answer
auto_answer is ASSIGNED but never sent: AUTO_SEND=false (shadow mode, S4 p.23); Builder C's act honours the flag.
"""
from __future__ import annotations

import os

from ..state import TicketState

QUEUES = {"payroll": "Payroll-Ops", "statutory": "Statutory-Compliance", "howto": "General-L1", "reports": "Reports-Analytics",
          "leave": "Leave-Attendance", "integration": "Integrations", "billing": "Billing", "access": "Access-Support",
          "onboarding": "Onboarding"}
QUEUE_LANE = frozenset({"payroll", "integration", "billing", "onboarding"})


def auto_send_enabled() -> bool:
    return os.environ.get("AUTO_SEND", "false").lower() == "true"


def decide_lane(s: TicketState) -> tuple[str, str | None, list[str]]:
    reasons = list(s.get("route_reasons", []))
    cat, pri = s.get("category") or "", s.get("priority", "P3")
    if s.get("is_injection"):
        return "block", None, reasons + ["rule1:injection->block", "attach_human_review"]
    if s.get("tenant_how") == "ambiguous" or not s.get("tenant_id"):
        return "human_review", None, reasons + ["rule2:tenant_ambiguous->human_review"]
    if s.get("incident_signal") or s.get("lane_hint") == "incident":
        return "incident", None, reasons + ["rule3:incident->incident"]
    if s.get("classify_lane") == "human" or not cat:
        return "human_review", None, reasons + ["rule4:classify_human->human_review"]
    if cat == "statutory":
        return "draft_for_agent", QUEUES[cat], reasons + [f"rule5:statutory->draft_for_agent({pri})"]
    if cat in QUEUE_LANE:
        return "route_queue", QUEUES[cat], reasons + [f"rule6:{cat}->route_queue({QUEUES[cat]},{pri})"]
    if pri == "P1":
        return "draft_for_agent", QUEUES.get(cat), reasons + [f"rule7:P1_{cat}->draft_for_agent"]
    return "auto_answer", QUEUES.get(cat), reasons + [f"rule7:{cat}_{pri}->auto_answer(AUTO_SEND={str(auto_send_enabled()).lower()})"]


def route(state: TicketState) -> TicketState:
    lane, queue, reasons = decide_lane(state)
    out: TicketState = dict(state)  # type: ignore[assignment]
    out.update(lane=lane, queue=queue, route_reasons=reasons,
               trace=list(state.get("trace", [])) + [{"node": "route", "lane": lane, "queue": queue, "reasons": reasons,
                                                       "tier": state.get("tenant_tier"), "priority": state.get("priority")}])
    return out
