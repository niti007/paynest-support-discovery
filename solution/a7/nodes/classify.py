"""Box 4: classify. Category, urgency, priority and injection flag from ONE small-LLM call (user decision 2026-11-12).

- Primary backend ROUTING_POLICY["auto_backend"] = Nova Lite. If it throttles, errors or returns no valid category, the
  fallback backend (Qwen3-32B) is asked instead; which backend answered is recorded in the trace and route_reasons. If both
  fail the ticket goes to human_review. auto_threshold is 0.0 (every ticket is auto-routed) and kept so it can be raised.
- The injection guard and any is_injection = yes still force a human (policy.route_decision).
- Priority is asked of the model with the SLA 3.2 definitions (not the customer's urgency wording). If the model gives no
  valid priority, the code falls back to derive_priority (urgency + category). No keyword rules.
- Privacy flag (escalation timer "DPO + Security within 1 h"): asked in the same call with the ESC wording. A plain-code
  fallback also sets it (pii_gate blocked, or the ticket names another customer's id). It only arms a notification timer.
- The Strands Decider is no longer on the runtime path (its code stays as teaching material; backend name "decider").
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from .. import config
from ..decide.base import INVALID, Q_CATEGORY, Q_URGENCY, DecideItem, classify_questions, get_decider
from ..decide.policy import route_decision
from ..state import TicketState

P1_CATEGORIES = frozenset({"payroll", "statutory", "integration"})

Q_PRIORITY = "priority"
PRIORITY_LEVELS = ("P1", "P2", "P3")
# SLA contract excerpt 3.2 (data-room/process/sla_contract_excerpt.md), verbatim.
SLA_P1 = "payroll cannot be run, or employees will not be paid on time, or statutory filing will be missed"
SLA_P2 = "material error in payroll output, or a core module is degraded with a workaround"
SLA_P3 = "how-to, reports, cosmetic, configuration, and everything else"
PRIORITY_QUESTION = ("What is the priority of this ticket by the SLA definition below, judged from what is actually affected "
                     "and NOT from how urgent the customer sounds? "
                     f"P1: {SLA_P1}. P2: {SLA_P2}. P3: {SLA_P3}.")


Q_PRIVACY = "privacy_concern"
# data-room/process/escalation_policy.md, row "Security or privacy concern (data shown to the wrong user, PII exposure)"
PRIVACY_QUESTION = ("Does this ticket report a security or privacy concern (data shown to the wrong user, PII exposure)? "
                    "Answer yes only if data was or may have been exposed to someone who should not see it: one customer's data "
                    "sent to or visible to another customer or person, another tenant's data visible to the customer, or a "
                    "suspected leak or breach. Answer no when the customer merely shares their OWN identifiers or personal data in the "
                    "ticket, for example their own ID, tax or account numbers (shown as placeholders like <AADHAAR_1> or <PAN_1>), "
                    "even when asking us to verify them: that is not exposure and is handled separately. "
                    "Also answer no when the customer asks how to configure or restrict access or permissions, or has any "
                    "ordinary support problem, even if it contains personal data.")
CUSTOMER_ID_RE = re.compile(r"\bC-\d{4}\b")


def privacy_fallback(state: Mapping[str, Any]) -> str | None:
    """Plain-code signals, independent of the model. There is no per-tenant owner on a vault entry (pii_vault is keyed by ticket),
    so the checks use what the gate and the text give: masking left PII behind (gate blocked), or the ticket names a
    customer id that is not the resolved tenant (data of another tenant in this ticket)."""
    if state.get("pii_gate_status") == "blocked":
        return "pii_gate_blocked"
    own = {x for x in (state.get("tenant_id"), state.get("customer_ref")) if x}
    others = set(CUSTOMER_ID_RE.findall(state.get("masked_text") or "")) - own
    if others and state.get("tenant_id"):
        return "other_tenant_id_in_text"
    return None


def derive_priority(category: str | None, urgency: str, is_payroll_window: bool = False) -> str:
    """Fallback only (the model gave no valid priority)."""
    if urgency == "urgent" and category in P1_CATEGORIES:
        return "P1"
    if urgency in ("urgent", "high") or category in ("payroll", "statutory"):
        return "P2"
    return "P3"


def classify_items(state: Mapping[str, Any], text: str | None = None) -> list[DecideItem]:
    items = classify_questions(dict(state), text=text)
    return items + [DecideItem(Q_PRIORITY, PRIORITY_QUESTION, list(PRIORITY_LEVELS), items[0].context, None),
                    DecideItem(Q_PRIVACY, PRIVACY_QUESTION, ["yes", "no"], items[0].context, None)]


def default_deciders(policy: dict | None = None) -> dict[str, Any]:
    p = policy or config.ROUTING_POLICY
    names = [p["auto_backend"], p.get("fallback_backend")]
    return {n: get_decider(n) for n in dict.fromkeys(n for n in names if n)}


def _run(decider: Any, items) -> dict[str, Any]:
    try:
        return decider.decide_multi(items)
    except Exception:  # noqa: BLE001 - a backend failure falls back / sends the ticket to a human, never crashes the graph
        return {}


def _usable(res: dict[str, Any]) -> bool:
    c = res.get(Q_CATEGORY)
    return c is not None and c.choice != INVALID


def make_classify(deciders: Mapping[str, Any] | None = None, policy: dict | None = None) -> Callable[[TicketState], TicketState]:
    p = policy or config.ROUTING_POLICY
    holder: dict[str, Any] = {"d": dict(deciders) if deciders else None}

    def classify(state: TicketState) -> TicketState:
        if holder["d"] is None:
            holder["d"] = default_deciders(p)
        ds = holder["d"]
        text = state["masked_text"]
        items = classify_items(state, text)
        tried: list[str] = []
        used, res = None, {}
        for name in dict.fromkeys(n for n in (p["auto_backend"], p.get("fallback_backend")) if n and n in ds):
            tried.append(name)
            r = _run(ds[name], items)
            if _usable(r):
                used, res = name, r
                break
            if used is None and not res:
                res = r
        # route_decision reads the backend named by the policy; point both roles at the backend that answered
        eff = {**p, "auto_backend": used or p["auto_backend"], "suggest_backend": used or p["auto_backend"]}
        rd = route_decision({eff["auto_backend"]: res}, text, state.get("language"), eff)
        cat = res.get(Q_CATEGORY) if used else None
        urg = res[Q_URGENCY].choice if used and res.get(Q_URGENCY) and res[Q_URGENCY].choice != INVALID else "normal"
        prio = res.get(Q_PRIORITY) if used else None
        category = rd.category or ""
        injection = any(r.startswith(("injection_guard", "is_injection")) for r in rd.reasons)
        reasons = list(rd.reasons)
        if used is None:
            reasons.append("classify_failed:" + "+".join(tried) if tried else "classify_failed")
        elif used != p["auto_backend"]:
            reasons.append(f"classify_fallback:{used}")
        if prio is not None and prio.choice != INVALID:
            priority, prio_by = prio.choice, "model"
        else:
            priority, prio_by = derive_priority(category or rd.suggested_category, urg, bool(state.get("is_payroll_window"))), "fallback_code"
        priv = res.get(Q_PRIVACY) if used else None
        priv_by = "model" if priv is not None and priv.choice == "yes" else privacy_fallback(state)
        if priv_by:
            reasons.append(f"privacy_concern:{priv_by}")
        out: TicketState = dict(state)  # type: ignore[assignment]
        out.update(
            privacy_concern=bool(priv_by) or bool(state.get("privacy_concern", False)),
            category=category,
            category_confidence=float(cat.confidence) if cat is not None and cat.choice != INVALID else 0.0,
            suggested_category=rd.suggested_category,
            urgency=urg,
            is_injection=injection,
            classify_lane=rd.lane,
            priority=priority,
            route_reasons=reasons,
            trace=list(state.get("trace", [])) + [{"node": "classify", "lane": rd.lane, "category": category, "backend": used,
                                                    "tried": tried, "priority_by": prio_by, "privacy_concern": priv_by, "urgency": urg, "reasons": reasons}],
        )
        return out

    return classify
