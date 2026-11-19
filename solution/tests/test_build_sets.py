import json
import re
from collections import Counter
from pathlib import Path

import pytest

from solution.a7.pii.patterns import LEGACY_PII, PATTERNS
from solution.a7.pii.scanner import scan

SETS = Path(__file__).resolve().parents[1] / "evals" / "sets"
COUNTS = {"routing": 1327, "routing_reassigned": 257, "stale": 48, "control": 29, "incident": 78, "smoke": 14, "safety": 120}
CATS = {"howto", "payroll", "integration", "statutory", "leave", "access", "reports", "onboarding", "billing"}


def load(name):
    return [json.loads(l) for l in (SETS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def sets():
    if not (SETS / "routing.jsonl").exists():
        pytest.skip("run solution/evals/build_sets.py first")
    return {n: load(n) for n in COUNTS}


def test_counts(sets):
    assert {n: len(r) for n, r in sets.items()} == COUNTS


def test_ids_unique_and_schema(sets):
    for name, recs in sets.items():
        ids = [r["id"] for r in recs]
        assert len(ids) == len(set(ids)), name
        for r in recs:
            assert {"id", "source", "input", "language", "expected", "meta"} <= set(r)
            assert r["language"] in ("en", "hi", "ar", "hinglish")
            assert r["expected"]["category"] in CATS and r["expected"]["urgency"] in ("urgent", "high", "normal")
            assert isinstance(r["expected"]["is_injection"], bool)
            assert "is_payroll_window" in r["meta"] and "has_person_mask" in r["meta"] and "reassigned" in r["meta"]


def test_routing_facts(sets):
    r = {x["id"]: x for x in sets["routing"]}
    assert "PN-101504" not in r and "PN-101501" not in r  # resubmits dropped
    assert r["PN-100402"]["expected"]["category"] == "statutory" and r["PN-100402"]["meta"]["misrouted"]
    assert r["PN-101333"]["language"] == "ar" and "<IBAN_" in r["PN-101333"]["input"]
    assert [x["id"] for x in sets["routing"] if x["expected"]["is_injection"]] == ["PN-101129"]
    assert Counter(x["expected"]["category"] for x in sets["routing"]) == {
        "howto": 406, "payroll": 229, "integration": 124, "statutory": 113, "leave": 99, "access": 95,
        "reports": 92, "onboarding": 88, "billing": 81}
    assert sum(1 for x in sets["routing"] if x["meta"]["misrouted"]) == 111
    assert sum(1 for x in sets["routing"] if x["meta"]["is_payroll_window"]) == 935
    assert all(x["meta"]["reassigned"] for x in sets["routing_reassigned"])


def test_stale_control_incident_smoke(sets):
    assert {"PN-100276", "PN-100921", "PN-101315", "PN-100402"} <= {x["id"] for x in sets["stale"]}
    assert not {x["id"] for x in sets["stale"]} & {x["id"] for x in sets["control"]}
    inc = Counter(x["meta"]["incident"] for x in sets["incident"])
    assert inc == {"meridian-2026-09-25": 30, "PNJ-4412": 48}
    smoke = {x["id"] for x in sets["smoke"]}
    assert {"PN-100402", "PN-101055", "PN-101333", "PN-101129"} <= smoke


def test_safety_composition(sets):
    s = sets["safety"]
    inj = [x for x in s if x["expected"]["is_injection"]]
    ben = [x for x in s if not x["expected"]["is_injection"]]
    assert len(inj) == len(ben) == 60
    assert any(x["id"] == "PN-101129" for x in inj)
    assert all(x["id"].startswith("PN-101129") for x in inj) and all(x["id"].startswith("SAFE-BENIGN-") for x in ben)
    assert {x["meta"].get("lang") or x["language"] for x in inj} >= {"en", "hi", "ar", "hinglish"}
    assert {x["meta"].get("style") for x in inj} >= {"override", "roleplay", "fake_system", "obfuscated", "markup", "approve"}
    assert {x["language"] for x in ben} == {"en", "hi", "ar", "hinglish"}
    assert any(re.search(r"ignore|instructions|system", x["input"], re.I) for x in ben)


def test_safety_is_deterministic(sets):
    assert [x["input"] for x in sets["safety"]][:3] == [x["input"] for x in load("safety")][:3]


def test_no_pii_and_masked(sets):
    for name, recs in sets.items():
        for r in recs:
            t = r["input"]
            for k, rx in LEGACY_PII.items():
                assert not rx.search(t), (name, r["id"], k)
            for p in PATTERNS:
                assert not p.regex.search(re.sub(r"<[A-Z]+_\d+>", " ", t)), (name, r["id"], p.type)
    # full scanner (regex + presidio + NER), on every distinct input
    seen = set()
    for recs in sets.values():
        for r in recs:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            hits = [h for h in scan(re.sub(r"<[A-Z]+_\d+>", " ", r["input"])) if h.type != "PERSON"]  # NER PERSON on masked text = false positives
            assert not hits, (r["id"], [h.type for h in hits])


def test_inputs_contain_placeholders_for_pii_tickets(sets):
    pii = [x for x in sets["routing"] if x["meta"]["pii_types"]]
    assert len(pii) >= 100
    assert all(re.search(r"<[A-Z]+_\d+>", x["input"]) for x in pii)


def test_split_stratified(sets):
    r = sets["routing"]
    assert all(x["split"] in ("calib", "eval") for x in r)
    n_cal = sum(1 for x in r if x["split"] == "calib")
    assert abs(n_cal / len(r) - 0.2) < 0.01
    for cat in CATS:
        grp = [x for x in r if x["expected"]["category"] == cat]
        share = sum(1 for x in grp if x["split"] == "calib") / len(grp)
        assert abs(share - 0.2) < 0.03, cat
    for lang in ("en", "hi", "ar", "hinglish"):
        grp = [x for x in r if x["language"] == lang]
        assert abs(sum(1 for x in grp if x["split"] == "calib") / len(grp) - 0.2) < 0.08, lang
