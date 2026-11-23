"""Held-out round 3 (h3: 64 wrong / 55 genuine statutory drafts, validator 2026-11-14) as regression tests, plus the grounding exemptions
(dates, ids, versions, ordinals), the Hinglish number words and the LLM stale-claim wiring.

Two layers (user decision 2026-11-14: output guard = code rules + an LLM check):
  OFFLINE  the code rules alone, against the three real rule chunks (copied here). The residual is pinned EXACTLY: the wrong drafts the rules
           do not flag (KNOWN_RULE_BYPASS) and the genuine drafts they reject (KNOWN_RULE_FALSE_REJECT). A change in either direction fails
           the test: re-measure, then update the sets on purpose.
  aws      test_guard_llm.py runs the LLM layer (Nova Lite) on the rule bypasses, and test_h3_llm_subset here on a few genuine ones.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from solution.a7.nodes import non_claims, stale_llm
from solution.a7.nodes.numbers import canonicalise, numbers_in
from solution.a7.nodes.output_guard import code_checks, make_output_guard
from solution.tests.p3_fakes import FakeDecider

CASES = json.loads((Path(__file__).parent / "data" / "h3_cases.json").read_text())
# the real kb_chunks rows (rule chunks of the shipped reference data)
CHUNKS = {
    "pf": {"source_id": "R-IN-PF-2026-08", "title": "EPF statutory wage ceiling raised", "effective_from": "2026-08-01",
           "body": "EPF statutory wage ceiling raised. Effective 2026-08-01. Was: INR 15,000 per month. Now: INR 25,000 per month. Source: EPFO circular (fictional). Status: in_force."},
    "wps": {"source_id": "R-UAE-WPS-2026-07", "effective_from": "2026-07-15",
            "title": "WPS SIF file format moves from v2 to v3 (new mandatory establishment-reference column, revised EDR record layout)",
            "body": "WPS SIF file format moves from v2 to v3 (new mandatory establishment-reference column, revised EDR record layout). Effective 2026-07-15. Was: SIF v2 (14 columns). Now: SIF v3 (16 columns, new EDR layout). Source: Central Bank / MoHRE notice (fictional). Status: in_force."},
    "gr": {"source_id": "R-UAE-GRAT-2026-01", "title": "End-of-service gratuity: clarification on part-time and fixed-term calculation", "effective_from": "2026-01-01",
           "body": "End-of-service gratuity: clarification on part-time and fixed-term calculation. Effective 2026-01-01. Was: 21 days basic per year (first 5 years). Now: unchanged; clarification only. Source: MoHRE guidance (fictional). Status: applied."},
}
ASOF = {"pf": "2026-09-10", "wps": "2026-08-05", "gr": "2026-03-05"}
TICKET = {"pf": "Our CA says the PF figures differ. What is the current EPF wage ceiling for our payroll?",
          "wps": "Our WPS SIF upload was rejected with E104. What format should the file have?",
          "gr": "How is end-of-service gratuity calculated for our part-time staff?"}

KNOWN_RULE_BYPASS = {"W027", "W031", "W118", "W119"}      # wrong drafts the code rules do not flag: the stale "v2" label stated as current
KNOWN_RULE_FALSE_REJECT = {                               # genuine drafts the code rules reject
    "G069", "G085", "G088", "G095",                       # the old value named as history in wording the rules cannot read
    "G072", "G112", "G114",                               # "12%" rate: true, but not in the evidence (grounding rule)
    "G101", "G102", "G107"}                               # "15 minutes" / "14 days": a number the draft invents (not in the evidence)


def state(case: dict) -> dict:
    r, ch = case["rule"], CHUNKS[case["rule"]]
    return {"masked_text": case.get("ticket") or TICKET[r], "language": case["lang"], "category": "statutory", "as_of": ASOF[r], "draft": case["draft"],
            "tool_numbers": [], "topic_rules": [ch["source_id"]], "trace": [], "route_reasons": [],
            "retrieved": [{"source_id": ch["source_id"], "kind": "rule", "effective_from": ch["effective_from"], "effective_to": None, "score": 1.0}],
            "context_chunks": [ch]}


def rules_flag(case: dict) -> bool:
    return any(f.startswith("numbers_not_grounded") for f in code_checks(state(case)))


def test_h3_counts():
    assert (sum(c["label"] == "wrong" for c in CASES), sum(c["label"] == "genuine" for c in CASES)) == (64, 55)


def test_h3_rules_only_residual_is_pinned():
    bypass = {c["id"] for c in CASES if c["label"] == "wrong" and not rules_flag(c)}
    false_rej = {c["id"] for c in CASES if c["label"] == "genuine" and rules_flag(c)}
    assert bypass == KNOWN_RULE_BYPASS, sorted(bypass ^ KNOWN_RULE_BYPASS)
    assert false_rej == KNOWN_RULE_FALSE_REJECT, sorted(false_rej ^ KNOWN_RULE_FALSE_REJECT)


# ---- what the grounding check no longer fires on ---------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Your ticket arrived on 14 September 2026.", "We will reply by 15 September.", "Received 14 Sep 2026, effective 2026-07-15.",
    "The change took effect on 15 July 2026.", "September 14, 2026", "14/09/2026", "आपका अनुरोध 14 सितंबर को मिला; हम 15 सितंबर तक जवाब देंगे।",
    "استلمنا طلبكم في 14 أغسطس وسنرد في 15 أغسطس.", "Aapka ticket 14 September ko aaya, hum 15 September tak reply denge.",
    "see PN-100402 and CL-202413, C-17", "per R-IN-PF-2026-08 and KB-0142", "version 3 of the circular", "SIF v3", "the 14th and 3rd of the month",
])
def test_non_claims_are_not_numbers(text):
    assert numbers_in(non_claims.for_grounding(text), include_small=True) == [], text


def test_real_numbers_survive_the_exemptions():
    assert numbers_in(non_claims.for_grounding("The ceiling is INR 25,000 from 1 August 2026, see PN-100402 (v3)."), include_small=True) == ["25000"]
    assert numbers_in(non_claims.for_grounding("The file has 14 columns."), include_small=True) == ["14"]
    assert numbers_in(non_claims.for_grounding("Contributions stay at 12% of wages."), include_small=True) == ["12"]      # percentages stay grounded


@pytest.mark.parametrize("text,want", [
    ("usme chaudah columns hote hain", "14"), ("pachchis hazaar rupaye, pandrah hazaar nahi", "25000"), ("solah columns", "16"),
    ("ek sau pachchis", "125"), ("pachas", "50"), ("satrah", "17"), ("atharah", "18"), ("unnis", "19"), ("baais", "22")])
def test_hinglish_number_words(text, want):
    assert want in canonicalise(text).split() or want in numbers_in(text, include_small=True)


def test_hinglish_everyday_words_stay_words():
    for t in ("do not use this", "saath mein", "teen log", "tera naam", "char"):
        assert numbers_in(t, include_small=True) == [], t


def test_unchanged_rule_keeps_its_old_value_as_evidence():
    """Gratuity: Was 21 days, Now unchanged. Restating 21 days is not a stale claim."""
    for c in CASES:
        if c["rule"] == "gr":
            assert not rules_flag(c), c["id"]


# ---- the LLM layer is wired into the guard (fake checker: offline) -----------------------------------------------------
def guard_with(verdict: stale_llm.StaleVerdict, st: dict, **kw):
    g = make_output_guard(FakeDecider(answers="yes"), stale_checker=lambda s: verdict, **kw)
    return g(st)


GOOD = {"rule": "wps", "lang": "en", "draft": "From 15 July 2026 the SIF uses v3 with 16 columns [R-UAE-WPS-2026-07, effective 15 Jul 2026]."}


def test_llm_flag_fails_the_guard_and_routes_to_human_after_retry():
    st = state(GOOD)
    out = guard_with(stale_llm.StaleVerdict(flag=True, reason="says v2 is current"), st, max_retries=0)
    assert not out["guard"]["passed"] and out["guard"]["failures"] == ["stale_claim_llm"]
    assert out["lane"] == "human_review" and "guard_failed->human_review" in out["route_reasons"]
    first = guard_with(stale_llm.StaleVerdict(flag=True), st)
    assert first["guard"]["failures"] == ["stale_claim_llm"] and not first["guard"]["final"]       # first failure: loop back to the agent once
    trace = next(t for t in out["trace"] if t["node"] == "output_guard")
    assert trace["stale_llm"]["flag"] is True


def test_llm_error_fails_closed():
    out = guard_with(stale_llm.StaleVerdict(flag=True, error="ThrottlingException"), state(GOOD), max_retries=0)
    assert out["guard"]["failures"] == ["stale_claim_llm:error:ThrottlingException"] and out["lane"] == "human_review"

    def boom(_s):
        raise RuntimeError("x")
    g = make_output_guard(FakeDecider(answers="yes"), max_retries=0, stale_checker=boom)
    assert g(state(GOOD))["guard"]["failures"] == ["stale_claim_llm:error:RuntimeError"]


def test_llm_clean_verdict_passes_and_records_usage():
    v = stale_llm.StaleVerdict(flag=False, backend="eu.amazon.nova-lite-v1:0", tokens_in=860, tokens_out=30, cost_usd=6.4e-05)
    out = guard_with(v, state(GOOD))
    assert out["guard"]["passed"], out["guard"]["failures"]
    t = next(t for t in out["trace"] if t["node"] == "output_guard")["stale_llm"]
    assert (t["backend"], t["tokens_in"], t["tokens_out"], t["cost_usd"]) == ("eu.amazon.nova-lite-v1:0", 860, 30, 6.4e-05)


def test_llm_runs_for_statutory_only():
    calls = []
    g = make_output_guard(FakeDecider(answers="yes"), stale_checker=lambda s: calls.append(1) or stale_llm.StaleVerdict())
    howto = {"masked_text": "How do I download payslips?", "language": "en", "category": "howto", "as_of": "2026-07-29", "draft": "See KB-0105.", "topic_rules": [],
             "trace": [], "route_reasons": [], "retrieved": [{"source_id": "KB-0105", "kind": "kb"}], "context_chunks": [], "tool_numbers": []}
    g(howto)
    assert calls == []
    g({**howto, "category": "statutory", "topic_rules": []})
    g({**howto, "category": "howto", "topic_rules": ["R-IN-PF-2026-08"]})
    assert len(calls) == 2


def test_llm_skipped_for_a_draft_with_pii():
    calls = []
    g = make_output_guard(FakeDecider(answers="yes"), max_retries=0, stale_checker=lambda s: calls.append(1) or stale_llm.StaleVerdict())
    st = {**state(GOOD), "draft": GOOD["draft"] + " Contact me at ahmed.k@example.com"}
    out = g(st)
    assert calls == [] and any(f.startswith("pii") for f in out["guard"]["failures"])


# ---- stale_llm.check itself (fake model, offline): tool call parsing, retry, fallback, fail closed ------------------------
class _Msg:
    def __init__(self, args, tin=100, tout=10):
        self.tool_calls = [{"name": stale_llm.TOOL_NAME, "args": args}] if args is not None else []
        self.usage_metadata = {"input_tokens": tin, "output_tokens": tout}


class _Model:
    def __init__(self, script):
        self.script = script
        self.calls = 0

    def bind_tools(self, tools, tool_choice=None):
        assert tools[0]["name"] == stale_llm.TOOL_NAME and tool_choice == stale_llm.TOOL_NAME
        return self

    def invoke(self, messages):
        self.calls += 1
        r = self.script.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def run_check(script_by_tier: dict, monkeypatch, st=None):
    monkeypatch.setattr(stale_llm, "_limiter", lambda: type("L", (), {"wait": lambda self: None})())
    models = {t: _Model(s) for t, s in script_by_tier.items()}
    return stale_llm.check(st or state(GOOD), make_llm=lambda tier: models[tier], sleep=lambda s: None), models


def test_check_parses_the_tool_call_and_prices_it(monkeypatch):
    v, _ = run_check({"cheap": [_Msg({"stale_claim": True, "reason": "v2 presented as current"}, 862, 34)]}, monkeypatch)
    assert v.flag and v.reason.startswith("v2") and v.backend == "eu.amazon.nova-lite-v1:0" and v.error is None
    assert (v.tokens_in, v.tokens_out) == (862, 34) and v.cost_usd == pytest.approx((862 * 0.065 + 34 * 0.26) / 1e6, abs=1e-6)


def test_check_retries_throttling_then_falls_back_to_qwen32b(monkeypatch):
    th = RuntimeError("ThrottlingException")
    v, m = run_check({"cheap": [th, th, th, th], "fallback": [_Msg({"stale_claim": False, "reason": "history only"})]}, monkeypatch)
    assert not v.flag and v.fallback_used and v.backend == "qwen.qwen3-32b-v1:0" and m["cheap"].calls == 4


def test_check_fails_closed_when_both_models_fail(monkeypatch):
    v, _ = run_check({"cheap": [RuntimeError("x")] * 4, "fallback": [_Msg(None), _Msg({"stale_claim": "maybe"})]}, monkeypatch)
    assert v.flag and v.error


def test_check_skips_without_a_rule_change(monkeypatch):
    gr = {"rule": "gr", "lang": "en", "draft": "Gratuity is unchanged."}
    v, m = run_check({"cheap": []}, monkeypatch, state(gr))
    assert v.skipped == "no_rule_change" and not v.flag and m["cheap"].calls == 0


def test_prompt_carries_was_now_effective_date_received_date_and_draft():
    ch = stale_llm.rule_changes(state(GOOD))
    p = stale_llm.build_prompt(ch, "2026-08-05", GOOD["draft"])
    for needle in ("R-UAE-WPS-2026-07", "2026-07-15", "SIF v2 (14 columns)", "SIF v3 (16 columns, new EDR layout)", "2026-08-05", "<draft>"):
        assert needle in p
