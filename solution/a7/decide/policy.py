"""Hybrid routing policy (user decision 2026-11-09, see config.ROUTING_POLICY). Pure: no I/O, no model calls."""
from __future__ import annotations

from dataclasses import dataclass, field

from solution.a7 import config
from solution.a7.decide.base import INVALID, Decision
from solution.a7.decide.injection_guard import needs_human_injection_review


@dataclass
class RouteDecision:
    lane: str  # "auto" | "human"
    category: str | None  # set only on the auto lane
    suggested_category: str | None  # the suggest_backend's category, attached for the human
    reasons: list[str] = field(default_factory=list)


def route_decision(decisions: dict[str, dict[str, Decision]], text: str, language: str | None, policy: dict | None = None) -> RouteDecision:
    p = policy or config.ROUTING_POLICY
    auto = decisions.get(p["auto_backend"], {})
    sug = decisions.get(p["suggest_backend"], {})
    cat = auto.get("category")
    s = sug.get("category")
    suggested = s.choice if s is not None and s.choice != INVALID else None
    reasons: list[str] = []
    guard, why = needs_human_injection_review(text, language)
    if guard:
        reasons.append(f"injection_guard:{why}")
    for backend, ds in decisions.items():
        inj = ds.get("is_injection")
        if inj is not None and inj.choice == "yes":
            reasons.append(f"is_injection:{backend}")
    if cat is None or cat.choice == INVALID:
        reasons.append("no_valid_auto_category")
    elif cat.confidence < p["auto_threshold"]:
        reasons.append(f"low_confidence:{cat.confidence:.3f}<{p['auto_threshold']}")
    if reasons:
        return RouteDecision("human", None, suggested, reasons)
    return RouteDecision("auto", cat.choice, suggested, [])
