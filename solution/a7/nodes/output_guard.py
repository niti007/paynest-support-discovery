"""Box 10: output_guard. Code checks, then one decide() check. A failing draft loops back to the agent once
(hard tier), then the ticket falls to human_review.

Code checks (all must pass):
 1. numbers grounded   every number in the draft appears in a retrieved chunk (valid on the ticket date), a tool output,
                       or the ticket itself. "Was: ..." sentences of rule chunks (the rule's old_value) are NOT evidence
                       (old values are stale): a draft may ASSERT one as current -> fail; it may MENTION one as superseded
                       ("16 columns instead of 14", "v2 no longer applies") -> pass. Direction-aware, rule-based: see stale_values.py. Any other ungrounded number fails.
 2. rules cited        every cited id (R-..., KB-...) is in the retrieved set; each cited rule's effective date is shown;
                       statutory drafts must cite a rule.
 3. validity           no retrieved chunk is outside its effective dates on the ticket date.
 4. no PII             pii/scanner.py finds nothing and no <TYPE_n> placeholder is echoed.
 5. language           the draft is in the ticket's language.
    The grounding check ignores what is not a claim about a value: dates (en/ar/hi), ticket and rule ids, version labels, small
    ordinals (non_claims.py); a rule whose "Now" is unchanged keeps its "Was" figures as evidence (gratuity).
 6. stale claim (LLM) statutory drafts only: stale_llm.py (Nova Lite, Qwen3-32B fallback) answers "does the draft present a superseded
                       value, version or layout as currently applicable on the received date?". A flag, or an LLM error (fail closed),
                       fails the guard with `stale_claim_llm`. The code rules above catch the clear-cut cases, the LLM the wording they miss.
Then decide(): "does the draft answer the ticket?" (yes/no) from the suggest backend.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable

from .. import config
from ..decide.base import get_decider
from ..lang import detect_language
from ..pii.patterns import PLACEHOLDER_RE
from ..pii.scanner import scan
from ..state import TicketState
from . import non_claims, stale_llm
from .numbers import DIGIT_MAP, numbers_in as _numbers_in
from .stale_values import VERSION_RE, CurrentValues, asserted_stale

ID_RE = re.compile(r"\b(?:R-[A-Z0-9]+(?:-[A-Z0-9]+)+|KB-\d{4})\b")
WAS_RE = re.compile(r"\bWas:.*?\.(?=\s|$)", re.S)
NOW_RE = re.compile(r"\bNow:.*?\.(?=\s|$)", re.S)
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
OK_LANGS = {"en": {"en", "hinglish"}, "hinglish": {"en", "hinglish", "hi"}, "hi": {"hi", "hinglish"}, "ar": {"ar"}}


def ascii_digits(text: str) -> str:
    return text.translate(DIGIT_MAP)


def numbers_in(text: str, keep: set[str] | frozenset[str] = frozenset(), include_small: bool = False) -> list[str]:
    """Canonical numbers in the text (digits of any script, words, 25k, 15 thousand, Rs/INR/AED...): see numbers.py."""
    return _numbers_in(text or "", keep, include_small)


def date_forms(iso: str) -> set[str]:
    d = date.fromisoformat(iso)
    m, mon = MONTHS[d.month - 1], MONTHS[d.month - 1][:3]
    out = {iso, f"{d.day:02d}/{d.month:02d}/{d.year}", f"{d.day}/{d.month}/{d.year}"}
    for mm in (m, mon, "sept" if d.month == 9 else mon):
        out |= {f"{d.day} {mm} {d.year}", f"{d.day:02d} {mm} {d.year}", f"{mm} {d.day}, {d.year}", f"{mm} {d.day} {d.year}"}
    return out


def date_shown(draft: str, iso: str) -> bool:
    low = re.sub(r"\s+", " ", ascii_digits(draft).lower().replace(".", ""))
    return any(f in low for f in (x.lower().replace(".", "") for x in date_forms(iso)))


def extract_citations(draft: str, retrieved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = {r["source_id"]: r for r in retrieved}
    seen: list[str] = []
    for i in ID_RE.findall(draft or ""):
        if i in by and i not in seen:
            seen.append(i)
    return [{"source_id": i, "effective_from": by[i].get("effective_from")} for i in seen]


UNCHANGED_RE = re.compile(r"\b(?:unchanged|no change|not changed|no changes|clarification only|same as before|remains? (?:the same|as is))\b", re.I)


def was_is_stale(body: str) -> bool:
    """False when the rule did not change the value ("Was: 21 days. Now: unchanged; clarification only."): then "Was" is still current."""
    was = " ".join(m.group(0) for m in WAS_RE.finditer(body or ""))
    now = " ".join(m.group(0) for m in NOW_RE.finditer(body or ""))
    if not was:
        return False
    if UNCHANGED_RE.search(now):
        return False
    wn, nn = set(numbers_in(was, include_small=True)), set(numbers_in(now, include_small=True))
    return not (wn and wn == nn)


def evidence_numbers(state: TicketState) -> set[str]:
    nums: set[str] = set(state.get("tool_numbers", []))
    nums |= set(numbers_in(state.get("masked_text", ""), include_small=True))
    for c in state.get("context_chunks", []):
        text = f'{c.get("source_id", "")} {c.get("title") or ""} {c.get("body", "")}'
        if was_is_stale(c.get("body", "")):
            text = WAS_RE.sub(" ", text)
        nums |= set(numbers_in(text, include_small=True))
        nums |= set(numbers_in(c.get("effective_from") or "", include_small=True))
    return nums


def stale_numbers(state: TicketState) -> set[str]:
    """Numbers that only occur in a rule's old value ("Was: ...") and nowhere in the current evidence."""
    old: set[str] = set()
    for c in state.get("context_chunks", []):
        if not was_is_stale(c.get("body", "")):
            continue
        for m in WAS_RE.finditer(f'{c.get("body", "")}'):
            old |= set(numbers_in(m.group(0), include_small=True))
    return old - evidence_numbers(state)


def current_values(state: TicketState, stale: set[str]) -> CurrentValues:
    """Numbers and version labels from each rule's "Now: ..." text (16, v3): what a superseded mention must point to."""
    nums: set[str] = set()
    labels: set[str] = set()
    for c in state.get("context_chunks", []):
        if not was_is_stale(c.get("body", "")):
            continue
        was = " ".join(m.group(0) for m in WAS_RE.finditer(c.get("body", ""))).lower()
        for m in NOW_RE.finditer(c.get("body", "")):
            nums |= set(numbers_in(m.group(0), include_small=True))
            labels |= {v.lower() for v in VERSION_RE.findall(m.group(0)) if v.lower() not in was}
    return CurrentValues(nums - stale, labels)


def asserted_stale_in(state: TicketState, stale: set[str]) -> set[str]:
    return asserted_stale(non_claims.for_stale(state.get("draft") or ""), stale, current_values(state, stale))


def rule_required(state: TicketState) -> bool:
    """A statutory draft must cite a rule only when one is IN FORCE at the ticket's received date for its region and topic
    (retrieve sets topic_rules). Before the rule's effective date the right answer is the KB article, so a KB citation is fine.
    A draft can never cite a not-yet-in-force rule: such a rule is not retrievable (validity filter) -> cited_not_retrieved.
    No topic_rules key (state not produced by retrieve): fail closed, require a rule."""
    rules = state.get("topic_rules")
    return True if rules is None else bool(rules)


def code_checks(state: TicketState) -> list[str]:
    draft = state.get("draft") or ""
    fails: list[str] = []
    if not draft.strip():
        return ["empty_draft"]
    # 1. numbers grounded
    ev = evidence_numbers(state)
    stale = stale_numbers(state)
    asserted = asserted_stale_in(state, stale)
    ungrounded = sorted({n for n in numbers_in(non_claims.for_grounding(draft), keep=stale) if n not in ev and (n not in stale or n in asserted)})
    if ungrounded:
        fails.append("numbers_not_grounded:" + ",".join(ungrounded))
    # 2. rule + date
    retrieved = {r["source_id"]: r for r in state.get("retrieved", [])}
    cited = list(dict.fromkeys(ID_RE.findall(draft)))
    for cid in cited:
        r = retrieved.get(cid)
        if r is None:
            fails.append(f"cited_not_retrieved:{cid}")
        elif r.get("kind") == "rule" and r.get("effective_from") and not date_shown(draft, r["effective_from"]):
            fails.append(f"rule_date_not_shown:{cid}")
    if state.get("category") == "statutory" and rule_required(state) and not any(retrieved.get(c, {}).get("kind") == "rule" for c in cited):
        fails.append("statutory_no_rule_cited")
    # 3. validity on the ticket date
    as_of = state.get("as_of")
    if as_of:
        for r in state.get("retrieved", []):
            if (r.get("effective_from") and r["effective_from"] > as_of) or (r.get("effective_to") and r["effective_to"] < as_of):
                fails.append(f"retrieved_outside_validity:{r['source_id']}")
    # 4. PII
    hits = scan(draft, presidio=True, ner=False)
    if hits:
        fails.append("pii:" + ",".join(sorted({h.type for h in hits})))
    if PLACEHOLDER_RE.search(draft):
        fails.append("placeholder_echo")
    # 5. language
    if state.get("language") and detect_language(ID_RE.sub(" ", draft)) not in OK_LANGS.get(state["language"], {state["language"]}):
        fails.append(f"language_mismatch:expected_{state['language']}")
    return fails


ANSWERS_Q = "Does the draft reply answer the customer's ticket (relevant, on topic, not a refusal)?"


NO_LLM_AFTER = ("empty_draft", "pii:", "placeholder_echo")      # a draft with PII never goes to a model


def make_output_guard(decider: Any | None = None, max_retries: int = 1,
                      stale_checker: Callable[[dict], stale_llm.StaleVerdict] | None = None) -> Callable[[TicketState], TicketState]:
    holder = {"d": decider}
    checker = stale_checker or stale_llm.check

    def output_guard(state: TicketState) -> TicketState:
        fails = code_checks(state)
        sv: stale_llm.StaleVerdict | None = None
        if stale_llm.applies(state) and not any(f.startswith(NO_LLM_AFTER) for f in fails):
            try:
                sv = checker(dict(state))
            except Exception as e:  # noqa: BLE001 - fail closed
                sv = stale_llm.StaleVerdict(flag=True, error=type(e).__name__)
            if sv.error:
                fails.append(f"stale_claim_llm:error:{sv.error}")
            elif sv.flag:
                fails.append("stale_claim_llm")
        if not fails:
            if holder["d"] is None:
                holder["d"] = get_decider(config.ROUTING_POLICY["suggest_backend"])
            ctx = f'<ticket>\n{state["masked_text"]}\n</ticket>\n<draft>\n{state["draft"]}\n</draft>'
            try:
                d = holder["d"].decide(ANSWERS_Q, ["yes", "no"], context=ctx)
                if d.choice != "yes":
                    fails.append("decide:draft_does_not_answer" if d.choice == "no" else "decide:invalid")
            except Exception as e:  # noqa: BLE001 - fail closed
                fails.append(f"decide:error:{type(e).__name__}")
        extra = {"stale_kb": [f.split(":", 1)[1] for f in fails if f.startswith("retrieved_outside_validity:")],
                 "missing_kb": not state.get("retrieved")}
        prev = state.get("guard") or {}
        retries = prev.get("retries", 0)
        out: TicketState = dict(state)  # type: ignore[assignment]
        if not fails:
            out["guard"] = {"passed": True, "failures": [], "retries": retries, "final": True, **extra}
        elif retries < max_retries:
            out["guard"] = {"passed": False, "failures": fails, "retries": retries + 1, "final": False, **extra}
        else:
            out["guard"] = {"passed": False, "failures": fails, "retries": retries, "final": True, **extra}
            out["lane"] = "human_review"
            out["route_reasons"] = list(state.get("route_reasons", [])) + ["guard_failed->human_review"]
        entry: dict[str, Any] = {"node": "output_guard", "passed": not fails, "failures": fails, "retries": retries}
        if sv is not None and not sv.skipped:
            entry["stale_llm"] = sv.trace()
        out["trace"] = list(state.get("trace", [])) + [entry]
        return out

    return output_guard
