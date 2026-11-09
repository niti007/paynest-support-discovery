"""Box 10: output_guard. Code checks, then one decide() check. A failing draft loops back to the agent once
(hard tier), then the ticket falls to human_review.

Code checks (all must pass):
 1. numbers grounded   every number in the draft appears in a retrieved chunk (valid on the ticket date), a tool output,
                       or the ticket itself. "Was: ..." sentences of rule chunks are NOT evidence (old values are stale).
 2. rules cited        every cited id (R-..., KB-...) is in the retrieved set; each cited rule's effective date is shown;
                       statutory drafts must cite a rule.
 3. validity           no retrieved chunk is outside its effective dates on the ticket date.
 4. no PII             pii/scanner.py finds nothing and no <TYPE_n> placeholder is echoed.
 5. language           the draft is in the ticket's language.
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

ID_RE = re.compile(r"\b(?:R-[A-Z0-9]+(?:-[A-Z0-9]+)+|KB-\d{4})\b")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_DIGITS = str.maketrans({**{chr(0x0660 + i): str(i) for i in range(10)}, **{chr(0x06F0 + i): str(i) for i in range(10)},
                         **{chr(0x0966 + i): str(i) for i in range(10)}})
WAS_RE = re.compile(r"\bWas:.*?\.(?=\s|$)", re.S)
LIST_MARK_RE = re.compile(r"(?m)^\s*\d+[.)]\s")
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
OK_LANGS = {"en": {"en", "hinglish"}, "hinglish": {"en", "hinglish", "hi"}, "hi": {"hi", "hinglish"}, "ar": {"ar"}}


def ascii_digits(text: str) -> str:
    return text.translate(_DIGITS)


def _norm_num(tok: str) -> str:
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", tok):
        tok = tok.replace(",", "")
    tok = tok.replace(",", ".")
    if re.fullmatch(r"\d+", tok):
        return str(int(tok))
    return tok.rstrip(".")


def numbers_in(text: str) -> list[str]:
    t = LIST_MARK_RE.sub(" ", ascii_digits(text or ""))
    return [_norm_num(m) for m in NUM_RE.findall(t)]


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


def evidence_numbers(state: TicketState) -> set[str]:
    nums: set[str] = set(state.get("tool_numbers", []))
    nums |= set(numbers_in(state.get("masked_text", "")))
    for c in state.get("context_chunks", []):
        nums |= set(numbers_in(WAS_RE.sub(" ", f'{c.get("source_id", "")} {c.get("title") or ""} {c.get("body", "")}')))
        nums |= set(numbers_in(c.get("effective_from") or ""))
    return nums


def code_checks(state: TicketState) -> list[str]:
    draft = state.get("draft") or ""
    fails: list[str] = []
    if not draft.strip():
        return ["empty_draft"]
    # 1. numbers grounded
    ev = evidence_numbers(state)
    ungrounded = sorted({n for n in numbers_in(draft) if n not in ev})
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
    if state.get("category") == "statutory" and not any(retrieved.get(c, {}).get("kind") == "rule" for c in cited):
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


def make_output_guard(decider: Any | None = None, max_retries: int = 1) -> Callable[[TicketState], TicketState]:
    holder = {"d": decider}

    def output_guard(state: TicketState) -> TicketState:
        fails = code_checks(state)
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
        prev = state.get("guard") or {}
        retries = prev.get("retries", 0)
        out: TicketState = dict(state)  # type: ignore[assignment]
        if not fails:
            out["guard"] = {"passed": True, "failures": [], "retries": retries, "final": True}
        elif retries < max_retries:
            out["guard"] = {"passed": False, "failures": fails, "retries": retries + 1, "final": False}
        else:
            out["guard"] = {"passed": False, "failures": fails, "retries": retries, "final": True}
            out["lane"] = "human_review"
            out["route_reasons"] = list(state.get("route_reasons", [])) + ["guard_failed->human_review"]
        out["trace"] = list(state.get("trace", [])) + [{"node": "output_guard", "passed": not fails, "failures": fails, "retries": retries}]
        return out

    return output_guard
