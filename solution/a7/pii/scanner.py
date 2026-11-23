"""Leak scanner: regex + legacy analysis.PII + Presidio. Used by tests, the leak-scan CLI and Phase 5.

Hits carry type, offsets and source only; never the matched value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

from ..lang import detect_language, nlp_language
from . import presidio_layer
from .masker import FREE_TEXT_KEYS
from .patterns import PLACEHOLDER_RE, Span, find_legacy_spans, find_regex_spans


@dataclass(frozen=True)
class Hit:
    type: str
    start: int
    end: int
    source: str
    score: float = 1.0


# Machine ids that merely LOOK like PII: a canonical UUID (LangGraph thread / checkpoint / run ids) or a long bare hex id (32+ chars:
# md5, sha, checkpoint ids). Their digit runs read as Aadhaar / phone numbers. Real identifiers never match: Aadhaar is 12 digits,
# phone <= 15, PAN and Emirates ID are short. An IBAN is up to 34 characters, so a COMPACT one of 24+ characters whose country code and
# digits all happen to be hex (e.g. an Andorran AD + 22 digits) COULD look like a long hex id: IBAN detection therefore runs on the raw text
# first and mask_ids() never blanks a span that is a valid IBAN (mod-97).
# Long hex ids (>= 24 hex chars with at least one letter, so a long digit string is NOT masked) may be glued to a word suffix inside checkpoints.
# A dashed UUID is unambiguous without word boundaries: msgpack-packed LangGraph checkpoints put the next field right after it ("...<uuid>a...").
ID_SPAN_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|(?<![0-9A-Za-z])(?=[0-9]*[a-fA-F])[0-9a-fA-F]{24,}")


# Generic IBAN: country code + 2 check digits + 11..30 alphanumerics (total 15..34), compact or in space-separated groups. The candidate must
# pass the ISO 13616 mod-97 check (remainder 1), which keeps ids, order numbers and similar strings out. The AE pattern in patterns.py
# (no checksum) stays as the first line; this covers every other country (DE, GB, FR, SA, ...). Upper-case letters only.
_IBAN_CAND_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}")


def _iban_mod97(compact: str) -> bool:
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def iban_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of every valid-mod-97 IBAN in text (any country, grouped or compact). Never returns the value."""
    out: list[tuple[int, int]] = []
    for m in _IBAN_CAND_RE.finditer(text):
        idx = [i for i in range(m.start(), m.end()) if text[i] != " "]
        for n in range(min(34, len(idx)), 14, -1):
            compact = "".join(text[i] for i in idx[:n])
            nxt_adjacent = n < len(idx) and idx[n] == idx[n - 1] + 1
            if not nxt_adjacent and _iban_mod97(compact):
                out.append((m.start(), idx[n - 1] + 1))
                break
    return out


def mask_ids(text: str) -> str:
    """Blank out UUID / long-hex-id spans with '_' (same length, so every offset stays valid) before scanning.
    A span that is a valid IBAN is left alone, so a long hex-looking IBAN is never swallowed."""
    keep = iban_spans(text)
    return ID_SPAN_RE.sub(lambda m: m.group() if any(m.start() < e and m.end() > b for b, e in keep) else "_" * len(m.group()), text)


def scan(text: str, *, presidio: bool = True, ner: bool = True, language: str | None = None) -> list[Hit]:
    if not text:
        return []
    ibans = [Span(b, e, "IBAN", "regex") for b, e in iban_spans(text)]     # on the raw text, before ids are blanked
    text = mask_ids(text)
    spans = find_regex_spans(text) + find_legacy_spans(text) + ibans
    if presidio:
        dl = language or detect_language(text)
        for s in presidio_layer.analyze(text, nlp_language(dl)):
            if s.type == "PERSON" and (not ner or dl == "hinglish"):
                continue
            spans.append(s)
    ph = [(m.start(), m.end()) for m in PLACEHOLDER_RE.finditer(text)]
    return [Hit(s.type, s.start, s.end, s.source, s.score) for s in spans
            if not any(s.start < e and s.end > b for b, e in ph)]


def scan_record(obj: Any, path: str = "", key: str | None = None, **kw) -> Iterator[tuple[str, Hit]]:
    """Yield (field path, hit) for every string in a record. NER only on free-text keys."""
    if isinstance(obj, str):
        free = key in FREE_TEXT_KEYS
        for h in scan(obj, ner=free, presidio=free, **kw):
            yield path, h
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from scan_record(v, f"{path}.{k}" if path else k, k, **kw)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from scan_record(v, f"{path}[{i}]", key, **kw)
