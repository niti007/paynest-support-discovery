"""Box 4: classify. Category, urgency and injection flag from decide() backends, via ROUTING_POLICY.

- The auto backend (ROUTING_POLICY["auto_backend"], the Strands Decider) answers category/urgency/is_injection.
- The suggest backend (Nova) answers the same questions; its category is attached for a human when the
  policy sends the ticket to a person (confidence < threshold, injection flag, injection guard, invalid answer).
- Priority is plain code from urgency and category (SLA 3.2 P1: payroll cannot run / pay at risk / statutory
  filing missed). Backends are injected; unit tests use fakes.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from .. import config
from ..decide.base import INVALID, Q_CATEGORY, Q_INJECTION, Q_URGENCY, classify_questions, get_decider
from ..decide.policy import route_decision
from ..state import TicketState

P1_CATEGORIES = frozenset({"payroll", "statutory", "integration"})


def derive_priority(category: str | None, urgency: str, is_payroll_window: bool = False) -> str:
    if urgency == "urgent" and category in P1_CATEGORIES:
        return "P1"
    if urgency in ("urgent", "high") or category in ("payroll", "statutory"):
        return "P2"
    return "P3"


def default_deciders(policy: dict | None = None) -> dict[str, Any]:
    p = policy or config.ROUTING_POLICY
    return {n: get_decider(n) for n in dict.fromkeys((p["auto_backend"], p["suggest_backend"]))}


def _run(decider: Any, items) -> dict[str, Any]:
    try:
        return decider.decide_multi(items)
    except Exception:  # noqa: BLE001 - a backend failure must send the ticket to a human, not crash the graph
        return {}


def make_classify(deciders: Mapping[str, Any] | None = None, policy: dict | None = None) -> Callable[[TicketState], TicketState]:
    p = policy or config.ROUTING_POLICY
    holder: dict[str, Any] = {"d": dict(deciders) if deciders else None}

    def classify(state: TicketState) -> TicketState:
        if holder["d"] is None:
            holder["d"] = default_deciders(p)
        ds = holder["d"]
        text = state["masked_text"]
        items = classify_questions(state, text=text)
        decisions = {name: _run(d, items) for name, d in ds.items()}
        rd = route_decision(decisions, text, state.get("language"), p)
        auto = decisions.get(p["auto_backend"], {})
        sug = decisions.get(p["suggest_backend"], {})
        cat = auto.get(Q_CATEGORY)
        urg = next((d[Q_URGENCY].choice for d in (auto, sug) if d.get(Q_URGENCY) and d[Q_URGENCY].choice != INVALID), "normal")
        injection = any(r.startswith(("injection_guard", "is_injection")) for r in rd.reasons)
        category = rd.category or ""
        out: TicketState = dict(state)  # type: ignore[assignment]
        out.update(
            category=category,
            category_confidence=float(cat.confidence) if cat is not None and cat.choice != INVALID else 0.0,
            suggested_category=rd.suggested_category,
            urgency=urg,
            is_injection=injection,
            classify_lane=rd.lane,
            priority=derive_priority(category or rd.suggested_category, urg, bool(state.get("is_payroll_window"))),
            route_reasons=list(rd.reasons),
            trace=list(state.get("trace", [])) + [{"node": "classify", "lane": rd.lane, "category": category,
                                                    "suggested": rd.suggested_category, "urgency": urg, "reasons": rd.reasons}],
        )
        return out

    return classify
