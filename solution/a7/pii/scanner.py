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
from .patterns import PLACEHOLDER_RE, find_legacy_spans, find_regex_spans


@dataclass(frozen=True)
class Hit:
    type: str
    start: int
    end: int
    source: str
    score: float = 1.0


# Machine ids that merely LOOK like PII: a canonical UUID (LangGraph thread / checkpoint / run ids) or a long bare hex id (32+ chars:
# md5, sha, checkpoint ids). Their digit runs read as Aadhaar / phone numbers. Real identifiers never match: Aadhaar is 12 digits,
# phone <= 15, IBAN <= 34 and has non-hex letters, PAN and Emirates ID are short.
ID_SPAN_RE = re.compile(r"(?<![0-9A-Za-z])(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32,})(?![0-9A-Za-z])")


def mask_ids(text: str) -> str:
    """Blank out UUID / long-hex-id spans with '_' (same length, so every offset stays valid) before scanning."""
    return ID_SPAN_RE.sub(lambda m: "_" * len(m.group()), text)


def scan(text: str, *, presidio: bool = True, ner: bool = True, language: str | None = None) -> list[Hit]:
    if not text:
        return []
    text = mask_ids(text)
    spans = find_regex_spans(text) + find_legacy_spans(text)
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
