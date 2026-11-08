"""Layer 1 of the PII gate: regex patterns. This is the guarantee; Presidio adds names on top.

Ported from trainer/analysis.py `PII` (kept verbatim as LEGACY_PII for the scanner) and extended with
emails, phones, bank account/IFSC and looser separators. The extended patterns use ASCII lookarounds
instead of \\b so they also fire when glued to Hindi or Arabic letters (\\w is Unicode-aware in Python).

Placeholder type codes (the brief fixes only IBAN and EID; the rest are our choice):
IBAN, EID, BANK, PAN, AADHAAR, EMAIL, PHONE, PERSON, USERREF.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---- verbatim copy of analysis.PII (used by the scanner as a floor) -----------------------------------------
LEGACY_PII = {
    "aadhaar_like": re.compile(r"\b\d{4} \d{4} \d{4}\b(?! \d)"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "emirates_id": re.compile(r"\b784-\d{4}-\d{7}-\d\b"),
    "iban": re.compile(r"\bAE\d{2} \d{4} \d{4} \d{4} \d{4} \d{3}\b"),
    "ifsc_account": re.compile(r"A/C \d{11}, IFSC [A-Z]{4}0\d{6}"),
}

_NA = r"(?<![A-Za-z0-9])"   # not preceded by a latin alnum
_NB = r"(?![A-Za-z0-9])"    # not followed by a latin alnum
_ND = r"(?<!\d)"
_NDA = r"(?!\d)"


@dataclass(frozen=True)
class PiiPattern:
    type: str
    regex: re.Pattern
    score: float = 1.0


# Order = priority when two patterns overlap (most specific first).
PATTERNS: tuple[PiiPattern, ...] = (
    # AE IBAN: AE + 2 check digits + 19 digits, any mix of single spaces/hyphens
    PiiPattern("IBAN", re.compile(_NA + r"AE\d{2}(?:[ -]?\d){19}" + _NDA)),
    # Emirates ID 784-YYYY-NNNNNNN-C (separators optional)
    PiiPattern("EID", re.compile(_ND + r"784[ -]?\d{4}[ -]?\d{7}[ -]?\d" + _NDA)),
    # Bank account (+ IFSC), or a bare IFSC code
    PiiPattern("BANK", re.compile(r"A/C(?: ?(?:No\.?|number))?[: ]*\d{9,18}(?:,? *IFSC[: ]*[A-Z]{4}0[A-Z0-9]{6})?")),
    PiiPattern("BANK", re.compile(r"IFSC[: ]*[A-Z]{4}0[A-Z0-9]{6}" + _NB)),
    PiiPattern("PAN", re.compile(_NA + r"[A-Z]{5}\d{4}[A-Z]" + _NB)),
    # Aadhaar: 12 digits grouped 4-4-4 by space/hyphen, or 12 contiguous digits starting 2-9
    PiiPattern("AADHAAR", re.compile(_ND + r"\d{4}[ -]\d{4}[ -]\d{4}" + r"(?!\d|[ -]\d)")),
    PiiPattern("AADHAAR", re.compile(_ND + r"[2-9]\d{11}" + _NDA)),
    PiiPattern("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    # Phones: international (+CC ...), Indian 10-digit mobile, UAE local 0[2-9]x
    PiiPattern("PHONE", re.compile(r"(?<![\w+])\+\d{1,3}(?:[ -]?\(?\d{1,5}\)?){2,4}" + _NDA)),
    PiiPattern("PHONE", re.compile(_ND + r"[6-9]\d{4}[ -]?\d{5}" + _NDA)),
    PiiPattern("PHONE", re.compile(_ND + r"0(?:5\d|[2-47-9])[ -]?\d{3}[ -]?\d{4}" + _NDA)),
)

REGEX_TYPES = tuple(dict.fromkeys(p.type for p in PATTERNS))
PLACEHOLDER_RE = re.compile(r"<([A-Z]+)_(\d+)>")


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    type: str
    source: str          # "regex" | "presidio" | "legacy"
    score: float = 1.0
    prio: int = 0        # lower wins on overlap


def find_regex_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for prio, p in enumerate(PATTERNS):
        for m in p.regex.finditer(text):
            spans.append(Span(m.start(), m.end(), p.type, "regex", p.score, prio))
    return spans


def find_legacy_spans(text: str) -> list[Span]:
    names = {"aadhaar_like": "AADHAAR", "pan": "PAN", "emirates_id": "EID", "iban": "IBAN", "ifsc_account": "BANK"}
    return [Span(m.start(), m.end(), names[k], "legacy") for k, rx in LEGACY_PII.items() for m in rx.finditer(text)]
