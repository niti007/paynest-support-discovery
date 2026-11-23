"""Language detection for A7: en / hi / ar / hinglish.

Method (no model, no network, deterministic):
1. Script ratio over alphabetic characters: Devanagari share >= 0.2 -> "hi",
   Arabic-script share >= 0.2 -> "ar".
2. Otherwise the text is Latin script. A small lexicon of unambiguous romanised-Hindi tokens
   (nahi, hai, kaise, karo, ...) decides "hinglish" (>= 1 distinct hit); else "en".

Used by the PII gate (to pick the spaCy model) and by normalise (the `language` field).
"""
from __future__ import annotations

import re

_DEV = re.compile(r"[ऀ-ॿ]")
_ARB = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_WORD = re.compile(r"[a-z]+")

# Tokens that are not ordinary English words. Short ambiguous ones (ka, ke, ki, se, par, ko, me) are excluded.
HINGLISH_LEXICON = frozenset("""
nahi nahin hai hain hoon kya kaise kyun kyu mera meri mere hamara hamare aapka aapke aapki karo karna karke
kiya diya gaya gayi gaye raha rahi rahe abhi jaldi bhejo bhej chahiye wala wali mujhe humein humko dikha
dikhai dikh aaya aayi nikal paise pagar kripya dhanyavad sahi galat kuch sab bahut thoda lekin toh
kab kahan kitna kitni kyunki isliye jab tak mein
""".split())


def script_ratios(text: str) -> tuple[float, float]:
    """Return (devanagari_share, arabic_share) of alphabetic characters."""
    letters = sum(1 for c in text if c.isalpha())
    if not letters:
        return 0.0, 0.0
    return len(_DEV.findall(text)) / letters, len(_ARB.findall(text)) / letters


def detect_language(text: str) -> str:
    dev, arb = script_ratios(text or "")
    if dev >= 0.2 and dev >= arb:
        return "hi"
    if arb >= 0.2:
        return "ar"
    toks = set(_WORD.findall((text or "").lower()))
    if toks & HINGLISH_LEXICON:
        return "hinglish"
    return "en"


def nlp_language(lang: str) -> str:
    """Language code the Presidio/spaCy layer should use (hinglish -> en model)."""
    return {"hi": "hi", "ar": "ar"}.get(lang, "en")
