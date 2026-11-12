"""Box 4: classify. Category, urgency, priority and injection flag from ONE small-LLM call (user decision 2026-11-12).

- Primary backend ROUTING_POLICY["auto_backend"] = Nova Lite. If it throttles, errors or returns no valid category, the
  fallback backend (Qwen3-32B) is asked instead; which backend answered is recorded in the trace and route_reasons. If both
  fail the ticket goes to human_review. auto_threshold is 0.0 (every ticket is auto-routed) and kept so it can be raised.
- The injection guard and any is_injection = yes still force a human (policy.route_decision).
- Priority is asked of the model with the SLA 3.2 definitions (not the customer's urgency wording). If the model gives no
  valid priority, the code falls back to derive_priority (urgency + category). No keyword rules.
- The Strands Decider is no longer on the runtime path (its code stays as teaching material; backend name "decider").
"""
from __future__ import annotations

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


def derive_priority(category: str | None, urgency: str, is_payroll_window: bool = False) -> str:
    """Fallback only (the model gave no valid priority)."""
    if urgency == "urgent" and category in P1_CATEGORIES:
        return "P1"
    if urgency in ("urgent", "high") or category in ("payroll", "statutory"):
        return "P2"
    return "P3"


def classify_items(state: Mapping[str, Any], text: str | None = None) -> list[DecideItem]:
    items = classify_questions(dict(state), text=text)
    return items + [DecideItem(Q_PRIORITY, PRIORITY_QUESTION, list(PRIORITY_LEVELS), items[0].context, None)]


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
        out: TicketState = dict(state)  # type: ignore[assignment]
        out.update(
            category=category,
            category_confidence=float(cat.confidence) if cat is not None and cat.choice != INVALID else 0.0,
            suggested_category=rd.suggested_category,
            urgency=urg,
            is_injection=injection,
            classify_lane=rd.lane,
            priority=priority,
            route_reasons=reasons,
            trace=list(state.get("trace", [])) + [{"node": "classify", "lane": rd.lane, "category": category, "backend": used,
                                                    "tried": tried, "priority_by": prio_by, "urgency": urg, "reasons": reasons}],
        )
        return out

    return classify
