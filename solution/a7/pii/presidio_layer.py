"""Layer 2 of the PII gate: Presidio (custom pattern recognizers + spaCy NER for person names).

Models: spaCy `en_core_web_sm` for en (and Hinglish), `xx_ent_wiki_sm` for hi and ar.
If Presidio or a model is unavailable the layer degrades to regex only and logs that once.

Business names are not PII. NER output is filtered: only PERSON is kept, and a PERSON span made
only of words from customers.csv company names or a small domain allow-list is dropped.
"""
from __future__ import annotations

import csv
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from .patterns import PATTERNS, Span

log = logging.getLogger("a7.pii.presidio")

LANGS = ("en", "hi", "ar")
NER_MODELS = {"en": "en_core_web_sm", "hi": "xx_ent_wiki_sm", "ar": "xx_ent_wiki_sm"}
NER_ENTITIES = ("PERSON",)
MIN_SCORE = 0.5

# Words that look like names to NER but are product / domain vocabulary.
DOMAIN_ALLOW = frozenset(w.lower() for w in """
PayNest Payslip Payslips WPS SIF EPF PF ESI TDS UAE India Dubai Mumbai Kestrel Meridian Falcon Hindi Arabic
Jira Slack Excel PDF Form Resolution Mol Tally Zoho QuickBooks SAP
""".split())

_company_words: frozenset[str] | None = None


def _data_room() -> Path:
    return Path(os.environ.get("A7_DATA_ROOM", Path(__file__).resolve().parents[3] / "data-room"))


def company_words() -> frozenset[str]:
    global _company_words
    if _company_words is None:
        words: set[str] = set()
        p = _data_room() / "data" / "customers.csv"
        if p.exists():
            with p.open(encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    words.update(w.lower() for w in re.findall(r"\w+", r["name"]))
        _company_words = frozenset(words)
    return _company_words


def _is_business_word_only(text: str) -> bool:
    toks = [t.lower() for t in re.findall(r"\w+", text)]
    allow = company_words() | DOMAIN_ALLOW
    return bool(toks) and all(t in allow for t in toks)


@lru_cache(maxsize=1)
def get_analyzer():
    """Build the AnalyzerEngine once. Returns None (with a log line) if Presidio/models are missing."""
    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        nlp = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": l, "model_name": m} for l, m in NER_MODELS.items()],
        }).create_engine()
        registry = RecognizerRegistry(supported_languages=list(LANGS))
        registry.add_nlp_recognizer(nlp)
        for i, p in enumerate(PATTERNS):
            for lang in LANGS:
                registry.add_recognizer(PatternRecognizer(
                    supported_entity=p.type, supported_language=lang, name=f"a7_{p.type}_{i}_{lang}",
                    patterns=[Pattern(f"{p.type}_{i}", p.regex.pattern, 0.95)]))
        return AnalyzerEngine(nlp_engine=nlp, registry=registry, supported_languages=list(LANGS))
    except Exception as e:  # noqa: BLE001 - degrade, never crash the gate
        log.warning("Presidio unavailable (%s: %s); PII gate running regex-only", type(e).__name__, e)
        return None


def available() -> bool:
    return get_analyzer() is not None


def analyze(text: str, lang: str = "en") -> list[Span]:
    """Presidio spans: custom pattern entities + PERSON. Empty list when Presidio is unavailable."""
    eng = get_analyzer()
    if eng is None or not text.strip():
        return []
    lang = lang if lang in LANGS else "en"
    entities = list(dict.fromkeys(p.type for p in PATTERNS)) + list(NER_ENTITIES)
    out: list[Span] = []
    for r in eng.analyze(text=text, language=lang, entities=entities):
        if r.entity_type in NER_ENTITIES:
            if r.score < MIN_SCORE or _is_business_word_only(text[r.start:r.end]):
                continue
            out.append(Span(r.start, r.end, r.entity_type, "presidio", r.score, 100))
        else:
            out.append(Span(r.start, r.end, r.entity_type, "presidio", r.score, 50))
    return out
