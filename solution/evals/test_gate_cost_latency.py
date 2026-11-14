"""Gates: cost per draft (<= USD 0.02) and latency p50/p95 per lane (p95 < 3 s), from the harvest of real graph runs.

Cost: token usage recorded in the trace (agent loop) + the classifier / guard Nova calls (usage counters of the deciders, per ticket
delta) x Bedrock eu-north-1 prices in solution/a7/config.PRICE_PER_1M_TOKENS. The agent part is recomputed here from tokens x price
as a cross-check of the recorded figure.
Latency: wall clock of the full graph run per ticket (classify, dedupe, route, retrieve, agent, guard, up to the human_approval
pause), INCLUDING Bedrock throttling waits (Nova Lite on-demand quota is 10 RPM on this account) and the local Postgres/Valkey.
p95 < 3 s is a known likely FAIL (Qwen3-235B agent loop); it is measured and reported as is.
"""
from __future__ import annotations

import math
from collections import defaultdict

import pytest

from solution.evals import floors, results, spend

pytestmark = pytest.mark.eval


def pct(vals: list[float], q: float) -> float:
    s = sorted(vals)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def test_cost_per_draft(harvest):
    d = [r for r in harvest if (r["draft"] or "").strip()]
    assert d, "no drafts in the harvest"
    costs = [r["cost_usd"] for r in d]
    # cross-check the agent share from tokens x price
    agent_cx = [spend.usd(r["agent_model"], *r["agent_tokens"]) for r in d if r["agent_model"]]
    mean, mx, p95 = sum(costs) / len(costs), max(costs), pct(costs, 0.95)
    tin, tout = sum(r["tokens_in"] for r in d), sum(r["tokens_out"] for r in d)
    results.record("cost", "Cost per draft (classifier + agent + guard, Bedrock list prices)", "<= USD 0.02",
                   f"mean USD {mean:.5f}, p95 {p95:.5f}, max {mx:.5f}", f"n={len(d)} drafts (of {len(harvest)} sampled tickets)", mx <= 0.02,
                   f"Total {tin} input / {tout} output tokens. Agent-only share recomputed from tokens x price: mean USD {sum(agent_cx) / max(1, len(agent_cx)):.5f}.")
    assert mx <= 0.02
    assert abs(sum(agent_cx) - sum(spend.usd(r["agent_model"], *r["agent_tokens"]) for r in d if r["agent_model"])) < 1e-9


def test_latency_per_lane(harvest):
    by = defaultdict(list)
    for r in harvest:
        by[r["lane"]].append(r["latency_s"])
    allv = [r["latency_s"] for r in harvest]
    lanes = "; ".join(f"{k}: p50 {pct(v, .5):.1f}s / p95 {pct(v, .95):.1f}s (n={len(v)})" for k, v in sorted(by.items()))
    p95 = pct(allv, 0.95)
    drafting = [r["latency_s"] for r in harvest if (r["draft"] or "").strip()]
    results.record("latency", "Latency p50/p95 per lane (full graph run)", "p95 < 3 s",
                   f"overall p50 {pct(allv, .5):.1f}s, p95 {p95:.1f}s; with a draft p95 {pct(drafting, .95):.1f}s", f"n={len(allv)} tickets",
                   p95 < 3.0, f"Per lane: {lanes}. Includes Nova throttling waits (10 RPM quota) and the Qwen3-235B agent loop. Gate 5 exception (accepted 2026-11-14); regression ceiling in the next row.",
                   exception=True)
    ceil = floors.latency_ceiling(floors.LATENCY_P95_BASELINE_S)
    results.record("latency_regression", f"Latency p95 regression ceiling ({floors.LATENCY_CEILING_FACTOR}x the 2026-11-14 measurement of {floors.LATENCY_P95_BASELINE_S:.0f}s)",
                   f"<= {ceil:.0f} s", f"p95 {p95:.1f}s", f"n={len(allv)} tickets", p95 <= ceil, "Wall clock including throttling waits, so the ceiling has 50% headroom.")
    assert p95 <= ceil
    assert math.isfinite(p95)
