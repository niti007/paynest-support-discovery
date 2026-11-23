"""Masking: replace PII with `<TYPE_n>` placeholders and return the vault entries.

Numbering is per ticket and per type, in order of first appearance. The same value (after
canonicalisation: case-folded, spaces/hyphens removed) always gets the same placeholder. One
PlaceholderCounter is shared by every field of a ticket, so `<IBAN_1>` means one value everywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..lang import detect_language, nlp_language
from . import presidio_layer
from .patterns import PLACEHOLDER_RE, Span, find_regex_spans

# Keys holding free text: regex + NER.
FREE_TEXT_KEYS = frozenset({"subject", "body", "title", "message", "summary", "details", "text", "note",
                            "resolution_note", "comment"})
# Keys that are PII as a whole (masked entirely if the regex does not recognise the format).
WHOLE_FIELD_KEYS = {"from_email": "EMAIL", "wa_contact": "PHONE", "user_ref": "USERREF"}
# Business names (not PII): regex only, no NER, so customer-name matching keeps working after the gate.
BUSINESS_NAME_KEYS = frozenset({"end_customer", "customer_hint"})


@dataclass(frozen=True)
class VaultEntry:
    ticket_id: str
    placeholder: str
    pii_type: str
    value: str = field(repr=False)


def _canon(value: str, typ: str) -> str:
    if typ in ("PERSON",):
        return re.sub(r"\s+", " ", value.strip().casefold())
    if typ == "EMAIL":
        return value.strip().casefold()
    return re.sub(r"[\s-]", "", value).upper()


class PlaceholderCounter:
    """Per-ticket allocator. Share one instance across all fields of a ticket."""

    def __init__(self) -> None:
        self._ids: dict[str, dict[str, int]] = {}

    def get(self, typ: str, value: str) -> tuple[str, bool]:
        table = self._ids.setdefault(typ, {})
        key = _canon(value, typ)
        new = key not in table
        if new:
            table[key] = len(table) + 1
        return f"<{typ}_{table[key]}>", new


def _resolve(spans: list[Span]) -> list[Span]:
    chosen: list[Span] = []
    for s in sorted(spans, key=lambda s: (s.prio, -(s.end - s.start), s.start)):
        if s.end > s.start and all(s.end <= c.start or s.start >= c.end for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda s: s.start)


def mask(text: str, ticket_id: str, counter: PlaceholderCounter, *, language: str | None = None,
         nlp: bool = True, use_presidio: bool = True) -> tuple[str, list[VaultEntry]]:
    """Mask `text`. Returns (masked_text, NEW vault entries created by this call)."""
    if not text:
        return text, []
    spans = find_regex_spans(text)
    # Presidio custom recognizers always run when available; PERSON (NER) only when nlp=True.
    dl = language or detect_language(text)
    lang = nlp_language(dl)
    for s in (presidio_layer.analyze(text, lang) if use_presidio else []):
        if s.type == "PERSON" and (not nlp or dl == "hinglish"):  # en NER is unreliable on romanised Hindi
            continue
        spans.append(s)
    # never re-mask existing placeholders
    ph = [(m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(text)]
    spans = [s for s in spans if not any(s.start < e and s.end > b for b, e in ph)]
    out, last, entries = [], 0, []
    for s in _resolve(spans):
        value = text[s.start:s.end]
        placeholder, new = counter.get(s.type, value)
        out.append(text[last:s.start])
        out.append(placeholder)
        last = s.end
        if new:
            entries.append(VaultEntry(ticket_id, placeholder, s.type, value))
    out.append(text[last:])
    return "".join(out), entries


def mask_whole(value: str, typ: str, ticket_id: str, counter: PlaceholderCounter) -> tuple[str, list[VaultEntry]]:
    placeholder, new = counter.get(typ, value)
    return placeholder, ([VaultEntry(ticket_id, placeholder, typ, value)] if new else [])


def _mask_str(key: str | None, value: str, ticket_id: str, counter: PlaceholderCounter) -> tuple[str, list[VaultEntry]]:
    if key in WHOLE_FIELD_KEYS:
        typ = WHOLE_FIELD_KEYS[key]
        masked, entries = mask(value, ticket_id, counter, nlp=False)
        if not PLACEHOLDER_RE.fullmatch(masked.strip()) :
            m2, e2 = mask_whole(value, typ, ticket_id, counter)
            return m2, entries + e2
        return masked, entries
    free = key in FREE_TEXT_KEYS
    return mask(value, ticket_id, counter, nlp=free, use_presidio=free)  # structured strings: regex only (speed)


def mask_record(obj: Any, ticket_id: str, counter: PlaceholderCounter, key: str | None = None) -> tuple[Any, list[VaultEntry]]:
    """Recursively mask every string in a record (deep copy)."""
    if isinstance(obj, str):
        return _mask_str(key, obj, ticket_id, counter)
    if isinstance(obj, dict):
        out, entries = {}, []
        for k, v in obj.items():
            out[k], e = mask_record(v, ticket_id, counter, k)
            entries += e
        return out, entries
    if isinstance(obj, list):
        out, entries = [], []
        for v in obj:
            m, e = mask_record(v, ticket_id, counter, key)
            out.append(m)
            entries += e
        return out, entries
    return obj, []
