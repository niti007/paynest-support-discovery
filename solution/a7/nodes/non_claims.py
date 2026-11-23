"""Numbers that are NOT factual claims: dates, ticket / rule ids, version labels, small ordinals, percentages.

The output guard's grounding check asks "is every number in the draft in the evidence?". A draft that says "we received your ticket on
14 September", "see PN-100402" or "version 3 of the circular" is not making a claim about a rule value, so these spans are blanked
BEFORE numbers are extracted (the date of the rule itself is checked separately by date_shown(); ids by cited_not_retrieved).

  for_grounding(text)  dates, ids, version labels, ordinals out.      Percentages stay: "12%" must still be in the evidence.
  for_stale(text)      dates, ids, ordinals, percentages out.         Version labels stay: "SIF v3" is how a current value is recognised.

Months are matched in English (also what Hinglish uses), Hindi (Devanagari) and Arabic (Gulf and Levant names).
Digits of other scripts are mapped to ASCII first. Each blanked span becomes one neutral word so sentence structure survives.
"""
from __future__ import annotations

import re

from .numbers import DIGIT_MAP

_EN = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|(?-i:May)|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_HI = r"जनवरी|फ़रवरी|फरवरी|मार्च|अप्रैल|अप्रेल|मई|जून|जुलाई|अगस्त|सितंबर|सितम्बर|अक्तूबर|अक्टूबर|नवंबर|नवम्बर|दिसंबर|दिसम्बर"
_AR = (r"يناير|فبراير|مارس|أبريل|ابريل|مايو|يونيو|يوليو|أغسطس|اغسطس|سبتمبر|أكتوبر|اكتوبر|نوفمبر|ديسمبر|"
       r"كانون الثاني|كانون الأول|كانون الاول|شباط|آذار|اذار|نيسان|أيار|ايار|حزيران|تموز|آب|أيلول|ايلول|تشرين الأول|تشرين الاول|تشرين الثاني")
_MONTH = rf"(?:{_EN}|{_HI}|{_AR})"
_END = r"(?![A-Za-z])"
_YEAR = r"(?:,?\s*\d{4})?"

DATE_RES = [re.compile(p, re.I) for p in (
    r"(?<!\d)\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?!\d)",                                               # 2026-07-15
    r"(?<!\d)\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}(?!\d)",                                             # 15/07/2026
    rf"(?<![\w.])\d{{1,2}}(?:st|nd|rd|th)?\s*(?:of\s+)?{_MONTH}{_END}{_YEAR}",                   # 14 September (2026), 14th of Sept
    rf"(?<![A-Za-z]){_MONTH}{_END}\s*\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s*\d{{4}})?",              # September 14, 2026
    rf"(?<![A-Za-z]){_MONTH}{_END},?\s*\d{{4}}(?!\d)",                                           # August 2026
)]
ID_RE = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z]{1,4}-)+\d[\w-]*")                                  # PN-100402, CL-202413, C-17, KB-0142, R-IN-PF-2026-08
VERSION_RES = [re.compile(p, re.I) for p in (
    r"(?<![A-Za-z0-9])v\d+(?:\.\d+)*(?![A-Za-z0-9])",
    r"(?<![A-Za-z])version\s*\d+(?:\.\d+)*",
    r"(?:वर्ज़न|वर्जन|संस्करण|الإصدار|الاصدار|إصدار|اصدار|النسخة|نسخة)\s*\d+(?:\.\d+)*")]
ORDINAL_RE = re.compile(r"(?<![\w.])\d{1,2}(?:st|nd|rd|th)(?![A-Za-z])", re.I)
PERCENT_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|percent|per cent|प्रतिशत|फीसदी|फ़ीसदी|بالمئة|بالمائة|في المئة|في المائة)", re.I)


def _blank(text: str, rx: re.Pattern[str], word: str) -> str:
    return rx.sub(f" {word} ", text)


def _common(text: str, date_word: str = "DATE") -> str:
    t = (text or "").translate(DIGIT_MAP)
    for rx in DATE_RES:
        t = _blank(t, rx, date_word)
    t = _blank(t, ID_RE, "ID")
    return _blank(t, ORDINAL_RE, "ORD")


def for_grounding(text: str) -> str:
    t = _common(text)
    for rx in VERSION_RES:
        t = _blank(t, rx, "VERSION")
    return t


def for_stale(text: str) -> str:
    # the date placeholder starts with a digit on purpose: stale_values reads "from 0DATE" like "from 1 August" (a 'current' cue),
    # and 0 is never a stale value
    return _blank(_common(text, "0DATE"), PERCENT_RE, "PCT")
