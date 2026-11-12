"""Direction-aware stale-value check (output_guard numbers check): a rule's old value may be MENTIONED as superseded, never ASSERTED.
Cases: the 41 wrong drafts + 11 genuine corrections from the Gate 3/4 re-validation, plus our own en/ar/hinglish/hindi cases."""
from __future__ import annotations

import pytest

from solution.a7.nodes.output_guard import code_checks
from solution.tests.p3_fakes import PF, RULE
from solution.tests.test_p3_nodes import agent_state

W, P = "R-UAE-WPS-2026-07", "R-IN-PF-2026-08"
cw = "[R-UAE-WPS-2026-07, effective 15 Jul 2026]"
cp = "[R-IN-PF-2026-08, effective 1 Aug 2026]"


def st(draft, rid):
    ch = RULE if rid == W else PF
    return agent_state(draft=draft, category="statutory", language="en",
                       retrieved=[{"source_id": rid, "kind": "rule", "effective_from": ch["effective_from"], "effective_to": None, "score": 1}],
                       context_chunks=[ch], masked_text="Question about the rule. Did the format change?",
                       as_of="2026-08-30" if rid == P else "2026-07-20")


def stale_passes(draft, rid, stale):
    f = code_checks(st(draft, rid))
    return not any(x.startswith("numbers_not_grounded") and stale in x.split(":")[1].split(",") for x in f)


# (draft, rule, stale number, genuine?)  genuine False = the stale value is asserted as current and must be caught
T = [
# genuine
("v2 (14 columns) no longer applies "+cw,W,"14",True),
("16 columns instead of 14 "+cw,W,"14",True),
("From 15 Jul 2026 the SIF uses v3 with 16 columns, replacing the 14-column v2 layout "+cw,W,"14",True),
("The ceiling is now INR 25,000, up from INR 15,000 "+cp,P,"15000",True),
("The old INR 15,000 ceiling no longer applies; it is INR 25,000 "+cp,P,"15000",True),
("SIF v3 has 16 columns. The previous 14-column layout is obsolete "+cw,W,"14",True),
# wrong, with markers
("Use 14 columns instead of 16 "+cw,W,"14",False),
("The SIF still has 14 columns, not the new layout "+cw,W,"14",False),
("Ignore v3; the file is 14 columns "+cw,W,"14",False),
("Previously 16 columns, now 14 "+cw,W,"14",False),
("The file was updated and has 14 columns "+cw,W,"14",False),
("Your file has 14 columns, which is the current format and the old layout is not valid "+cw,W,"14",False),
("The SIF has 14 columns, no longer 16 "+cw,W,"14",False),
("The SIF format changed from v1 to v2 with 14 columns "+cw,W,"14",False),
("Generate the SIF with 14 columns rather than 16 "+cw,W,"14",False),
("Use the 14 column format instead of the new one "+cw,W,"14",False),
("The format has not changed: it is still 14 columns "+cw,W,"14",False),
("The new format replaced the old one, it has 14 columns "+cw,W,"14",False),
("Earlier versions had issues, but the current SIF has 14 columns "+cw,W,"14",False),
("The SIF has 14 columns. Please do not use the 16 column version "+cw,W,"14",False),
("SIF v3 was cancelled; the file keeps 14 columns "+cw,W,"14",False),
("The SIF format was never changed, so 14 columns apply "+cw,W,"14",False),
# plain
("The SIF has 14 columns "+cw,W,"14",False),
("Ceiling is INR 15,000 per month "+cp,P,"15000",False),
# INR with markers
("The EPF ceiling is INR 15,000 instead of INR 25,000 "+cp,P,"15000",False),
("The EPF ceiling stays INR 15,000, not the new amount "+cp,P,"15000",False),
("Ignore the circular; the ceiling is INR 15,000 "+cp,P,"15000",False),
("Previously INR 25,000, now INR 15,000 "+cp,P,"15000",False),
("The ceiling was raised but your wages are capped at INR 15,000 "+cp,P,"15000",False),
("Use INR 15,000 rather than INR 25,000 for the wage cap "+cp,P,"15000",False),
("The wage ceiling is 15,000 per month; the old rules do not apply "+cp,P,"15000",False),
# Arabic
("يحتوي ملف SIF على 14 عمودا بدلا من 16 "+cw,W,"14",False),
("لا يزال الملف يحتوي على 14 عمودا وليس التخطيط الجديد "+cw,W,"14",False),
("كان الملف 14 عمودا وما زال كذلك "+cw,W,"14",False),
("الملف الآن 14 عمودا بعد أن كان 16 "+cw,W,"14",False),
("استخدم 14 عمودا بدلا من التخطيط الجديد "+cw,W,"14",False),
("الحد الأقصى للأجر هو 15,000 روبية بدلا من 25,000 "+cp,P,"15000",False),
("لم يعد الحد 25,000؛ الحد هو 15,000 "+cp,P,"15000",False),
# Arabic genuine
("لم يعد تخطيط v2 (14 عمودا) ساريا؛ الملف الجديد 16 عمودا "+cw,W,"14",True),
("الملف يحتوي على 16 عمودا بدلا من 14 "+cw,W,"14",True),
# Hinglish
("SIF file mein 14 columns hain, 16 ki jagah "+cw,W,"14",False),
("Purana nahi, abhi bhi 14 columns hi hain "+cw,W,"14",False),
("Pehle 16 columns the, ab 14 columns use karo "+cw,W,"14",False),
("File 14 columns ki hi rakho, naya layout ab nahi "+cw,W,"14",False),
("EPF ceiling INR 15,000 hi hai, 25,000 ki jagah "+cp,P,"15000",False),
("Pehle 25,000 tha, ab INR 15,000 hai "+cp,P,"15000",False),
# Hinglish genuine
("Naya SIF v3 mein 16 columns hain, purane 14 ki jagah "+cw,W,"14",True),
("EPF ceiling ab INR 25,000 hai; purana INR 15,000 ab nahi chalega "+cp,P,"15000",True),
# Hindi
("SIF फ़ाइल में 14 कॉलम हैं, 16 के बजाय "+cw,W,"14",False),
("पुराना 14 कॉलम वाला फ़ॉर्मेट अब लागू नहीं है "+cw,W,"14",True),
# multi-sentence evasion: marker in other sentence
("The old layout is gone. The file has 14 columns "+cw,W,"14",False),
("The file has 14 columns; the new layout is not used here "+cw,W,"14",False),
]

OWN = [
    # English
    ("The SIF now has 16 columns; the 14-column file is obsolete "+cw,W,"14",True),
    ("Do not use the 14 column layout any more "+cw,W,"14",True),
    ("Up to 14 July the SIF had 14 columns, but from 15 Jul 2026 SIF v3 with 16 columns applies "+cw,W,"14",False),  # unmarked history: direction unclear -> fails closed,
    ("EPF ceiling: INR 15,000 -> INR 25,000 "+cp,P,"15000",True),
    ("The ceiling changed from INR 15,000 to INR 25,000 "+cp,P,"15000",True),
    ("Wages are now capped at INR 25,000 (formerly INR 15,000) "+cp,P,"15000",True),
    ("SIF v3 replaces v2 (14 columns) "+cw,W,"14",True),
    ("Your file needs 14 columns "+cw,W,"14",False),
    ("INR 15,000 -> INR 25,000 is wrong, the ceiling is INR 15,000 "+cp,P,"15000",False),
    ("The ceiling changed from INR 25,000 to INR 15,000 "+cp,P,"15000",False),
    ("Wages are capped at INR 15,000 (formerly INR 25,000) "+cp,P,"15000",False),
    ("Earlier INR 25,000, today INR 15,000 "+cp,P,"15000",False),
    ("Keep 14 columns; v3 is not in force yet "+cw,W,"14",False),
    ("Up from 16 columns, the SIF is now 14 "+cw,W,"14",False),
    ("The 14-column layout is not obsolete "+cw,W,"14",False),
    ("SIF v3 replaces nothing, so 14 columns is right "+cw,W,"14",False),
    ("The old layout and the SIF with 14 columns are both fine "+cw,W,"14",False),
    # Arabic
    ("الملف الجديد 16 عمودا، ولم يعد تخطيط 14 عمودا ساريا "+cw,W,"14",True),
    ("الحد الأقصى الآن 25,000 روبية بعد أن كان 15,000 "+cp,P,"15000",True),
    ("يجب استخدام 16 عمودا وليس 14 "+cw,W,"14",True),
    ("يجب استخدام 14 عمودا وليس 16 "+cw,W,"14",False),
    ("كان الحد 25,000 والآن 15,000 "+cp,P,"15000",False),
    ("الحد الأقصى من 25,000 إلى 15,000 "+cp,P,"15000",False),
    ("الملف 14 عمودا كما هو "+cw,W,"14",False),
    # Hinglish / Hindi
    ("Ab SIF v3 mein 16 columns hain; purana 14 columns wala layout ab nahi chalega "+cw,W,"14",True),
    ("Pehle 15,000 tha, ab INR 25,000 hai "+cp,P,"15000",True),
    ("14 ki jagah 16 columns use karo "+cw,W,"14",True),
    ("16 ki jagah 14 columns use karo "+cw,W,"14",False),
    ("Ceiling INR 15,000 hai, naya amount nahi "+cp,P,"15000",False),
    ("SIF file 14 columns ki hi hogi "+cw,W,"14",False),
    ("अब SIF में 16 कॉलम हैं, पुराना 14 कॉलम वाला फ़ॉर्मेट लागू नहीं है "+cw,W,"14",True),
    ("सीमा 15,000 है "+cp,P,"15000",False),
    ("पहले 25,000 थी, अब 15,000 है "+cp,P,"15000",False),
]


@pytest.mark.parametrize("draft,rid,stale,genuine", T[:], ids=[f"t{i}" for i in range(len(T))])
def test_gate34_cases(draft, rid, stale, genuine):
    assert stale_passes(draft, rid, stale) == genuine, draft


@pytest.mark.parametrize("draft,rid,stale,genuine", OWN, ids=[f"own{i}" for i in range(len(OWN))])
def test_own_cases(draft, rid, stale, genuine):
    assert stale_passes(draft, rid, stale) == genuine, draft


def test_case_counts():
    assert (sum(1 for t in T if not t[3]), sum(1 for t in T if t[3])) == (41, 11)
    assert len(OWN) >= 15
