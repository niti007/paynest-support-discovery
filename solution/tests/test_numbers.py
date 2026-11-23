"""Number normaliser: every spelling of a number reaches the guard as one canonical value."""
from __future__ import annotations

import pytest

from solution.a7.nodes.numbers import canonicalise, find_mentions, numbers_in

SAME = [
    ("14", ["14 columns", "fourteen columns", "١٤ عمود", "१४ कॉलम", "أربعة عشر عموداً", "चौदह कॉलम", "FOURTEEN columns"]),
    ("15000", ["15,000", "15000", "15k", "15K", "15 thousand", "fifteen thousand", "INR 15,000", "Rs. 15,000", "₹15,000", "AED 15,000",
               "د.إ 15,000", "15,000 rupees", "15 hazaar", "15 hazar", "पंद्रह हज़ार", "पन्द्रह हजार", "१५,००० रुपये", "١٥٬٠٠٠".replace("٬", ","),
               "خمسة عشر ألف", "0.15 lakh", "15 हज़ार", "15 ألف"]),
    ("25000", ["25k", "25 thousand", "twenty five thousand", "twenty-five thousand", "पच्चीस हज़ार", "خمسة وعشرون ألف", "0.25 lakh"]),
    ("200000", ["2 lakh", "2 lac", "two lakh", "दो लाख", "200,000", "two hundred thousand"]),
    ("10000000", ["1 crore", "one crore", "एक करोड़", "10 million"]),
    ("150", ["one hundred and fifty", "एक सौ पचास", "150"]),
    ("100000", ["one hundred thousand", "1 lakh", "100k"]),
    ("16", ["sixteen", "ستة عشر", "सोलह"]),
    ("100", ["hundred", "सौ", "مئة"]),
]


@pytest.mark.parametrize("value,spellings", SAME, ids=[v for v, _ in SAME])
def test_every_spelling_gives_the_same_value(value, spellings):
    for text in spellings:
        got = [m.value for m in find_mentions(text)]
        assert got == [value], (text, got)


def test_currency_is_recognised_not_part_of_the_value():
    assert [m.currency for m in find_mentions("INR 15,000")] == [True]
    assert [m.currency for m in find_mentions("fifteen thousand rupees")] == [True]
    assert [m.currency for m in find_mentions("15,000 columns")] == [False]


def test_canonicalise_rewrites_words_and_keeps_plain_digits():
    assert canonicalise("fourteen columns, not 16, 15k") == "14 columns, not 16, 15000"
    assert canonicalise(canonicalise("पंद्रह हज़ार")) == "15000"


def test_small_word_numbers_are_prose_for_grounding_only():
    assert numbers_in("one moment, two options") == []
    assert numbers_in("one moment", keep={"1"}) == ["1"]
    assert numbers_in("fourteen options") == ["14"]
    assert numbers_in("one moment", include_small=True) == ["1"]


def test_dates_and_versions_still_give_their_digits():
    assert numbers_in("SIF v3 from 15 July 2026") == ["3", "15", "2026"]
