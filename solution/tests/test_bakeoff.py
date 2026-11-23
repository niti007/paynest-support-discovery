import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from solution.evals import bakeoff as bo  # noqa: E402

CATS = ["payroll", "statutory", "howto", "billing"]


def test_ece_known_example():
    rows = [(True, 0.8)] * 6 + [(False, 0.8)] * 4  # acc 0.6, conf 0.8 -> ECE 0.2
    assert bo.ece(rows) == pytest.approx(0.2)
    rows2 = [(True, 1.0)] * 5 + [(False, 0.1)] * 5  # perfect calibration at the edges (1.0 in top bin)
    assert bo.ece(rows2) == pytest.approx(0.5 * 0.0 + 0.5 * 0.1)
    assert bo.ece([]) is None


def test_reliability_bins():
    rel = bo.reliability([(True, 0.95), (False, 0.15), (True, 1.0)])
    assert len(rel) == 10 and rel[9]["n"] == 2 and rel[1]["n"] == 1


def test_sweep_and_target():
    rows = [(True, 0.99)] * 8 + [(False, 0.6)] * 2 + [(True, 0.55)] * 2
    sw = {x["threshold"]: x for x in bo.sweep(rows)}
    assert sw[0.5]["human_share"] == 0 and sw[0.5]["auto_accuracy"] == pytest.approx(10 / 12)
    assert sw[0.6]["human_share"] == pytest.approx(2 / 12) and sw[0.6]["auto_accuracy"] == pytest.approx(8 / 10)
    assert sw[0.95]["auto_accuracy"] == 1.0 and sw[0.95]["human_share"] == pytest.approx(4 / 12)
    t = bo.threshold_for_target(rows)
    assert t["reachable"] and t["threshold"] == pytest.approx(0.61) and t["human_share"] == pytest.approx(4 / 12)
    assert not bo.threshold_for_target([(False, 0.9)])["reachable"]


def test_bounce():
    b = bo.bounce([(True, .9)] * 6 + [(False, .9)] * 4, [(True, .9)] * 9 + [(False, .9)])
    assert b["acc_window"] == pytest.approx(0.6) and b["acc_rest"] == pytest.approx(0.9)
    assert b["accuracy_drop"] == pytest.approx(0.3) and b["misroute_window"] == pytest.approx(0.4)


def test_injection_metrics():
    m = bo.injection_metrics([("PN-101129", "yes", "yes"), ("PN-101129-v1", "yes", "no"), ("x", "no", "yes"), ("y", "no", "no")])
    assert m["recall"] == 0.5 and m["fpr"] == 0.5 and m["pn_101129_n"] == 2 and not m["pn_101129_all_caught"]


def build_fixture(root: Path):
    """Synthetic sets + preds for 'good' (accurate, calibrated) and 'weak' (misses injections)."""
    rnd = random.Random(7)
    sets_dir, rep = root / "sets", root / "reports"
    sets_dir.mkdir()
    rep.mkdir()
    routing, safety = [], []
    for i in range(120):
        lang = ["en", "hi", "ar", "hinglish"][i % 4]
        routing.append({"id": f"R{i}", "source": "email", "input": "masked text " * 10, "language": lang,
                        "expected": {"category": CATS[i % 4], "urgency": "high" if i % 5 == 0 else "low", "is_injection": False},
                        "meta": {"is_payroll_window": i % 3 == 0, "has_person_mask": i % 6 == 0, "reassigned": False}, "split": "eval"})
    for i in range(10):
        safety.append({"id": "PN-101129" if i == 0 else f"S{i}", "source": "email", "input": "ignore rules", "language": "en",
                       "expected": {"category": "payroll", "urgency": "low", "is_injection": i < 5},
                       "meta": {"is_payroll_window": False, "has_person_mask": False, "reassigned": False}, "split": "eval"})
    for name, recs in (("routing", routing), ("safety", safety)):
        (sets_dir / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
    for backend, acc, inj_ok in (("good", 0.9, True), ("weak", 0.6, False)):
        for name, recs in (("routing", routing), ("safety", safety)):
            lines = []
            for r in recs:
                ok = rnd.random() < acc
                cat = r["expected"]["category"] if ok else "billing" if r["expected"]["category"] != "billing" else "howto"
                inj = "yes" if r["expected"]["is_injection"] and (inj_ok or r["id"] != "PN-101129") else "no"
                lines += [
                    {"id": r["id"], "name": "category", "choice": cat, "confidence": 0.9 if ok else 0.55, "latency_ms": 10.0},
                    {"id": r["id"], "name": "urgency", "choice": r["expected"]["urgency"], "confidence": 0.8, "latency_ms": 10.0},
                    {"id": r["id"], "name": "is_injection", "choice": inj, "confidence": 0.9, "latency_ms": 10.0}]
            (rep / f"preds_{backend}_{name}.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    return sets_dir, rep


def test_bakeoff_end_to_end_on_fixture(tmp_path):
    sets_dir, rep = build_fixture(tmp_path)
    res = bo.run_bakeoff(["good", "weak", "bedrock_nova"], ["routing", "safety", "stale"], True, sets_dir, rep)
    assert res["sets"]["stale"]["skipped"]
    r = res["sets"]["routing"]
    assert r["missing_preds"] == ["bedrock_nova"] and r["common_n"] == 120
    good = r["per_backend"]["good"]
    assert good["n"] == 120 and good["questions"]["category"]["accuracy"] > r["per_backend"]["weak"]["questions"]["category"]["accuracy"]
    assert set(good["by_language"]) == {"en", "hi", "ar", "hinglish"} and good["by_language"]["en"]["n"] == 30
    assert good["latency_ms_per_ticket"]["p50"] == 30.0
    assert res["sets"]["safety"]["per_backend"]["weak"]["injection"]["pn_101129_all_caught"] is False
    assert res["winner"]["winner"] == "good"
    assert res["cost"]["decider"]["usd_month"] > 50 if "decider" in res["cost"] else True


def test_cli_writes_both_reports(tmp_path):
    sets_dir, rep = build_fixture(tmp_path)
    import subprocess

    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, str(root / "solution/evals/bakeoff.py"), "--backends", "good,weak", "--sets", "routing,safety",
                    "--reuse-preds", "--sets-dir", str(sets_dir), "--reports-dir", str(rep)], check=True, cwd=root)
    md = (rep / "bakeoff.md").read_text()
    for section in ("Winner", "Cost (USD per month)", "Payroll-window bounce", "Language subsets", "PII over-masking",
                    "Calibration", "Low-confidence-to-human sweep", "Injection", "Latency per ticket"):
        assert section in md, section
    js = json.loads((rep / "bakeoff.json").read_text())
    assert js["winner"]["winner"] == "good"


def test_errored_rows_excluded_and_counted(tmp_path):
    sets_dir, rep = build_fixture(tmp_path)
    lines = [json.loads(x) for x in (rep / "preds_good_routing.jsonl").read_text().splitlines()]
    for x in lines:
        if x["id"] in {f"R{i}" for i in range(20)}:
            x.update(choice="__invalid__", confidence=0.0, error="LoginRefreshRequired")
    (rep / "preds_good_routing.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
    res = bo.run_bakeoff(["good", "weak"], ["routing", "safety"], True, sets_dir, rep)
    g = res["sets"]["routing"]["per_backend"]["good"]
    assert g["n_error"] == 20 and g["n_clean"] == 100 and g["questions"]["category"]["n"] == 100
    assert res["sets"]["routing"]["common_n"] == 100
    assert g["calib_threshold"]["n_calib"] == 0 and not g["calib_threshold"]["reachable"]


def test_pn_original_separate_and_guard():
    m = bo.injection_metrics([("PN-101129", "yes", "yes"), ("PN-101129-v01", "yes", "no"), ("PN-101129-v02", "yes", "yes")])
    assert m["pn_original_caught"] is True and m["pn_variants_n"] == 2 and m["pn_variants_recall"] == 0.5


def test_calib_threshold_chosen_on_calib_only():
    mk = lambda split, ok, conf: ({"split": split}, ok, conf)
    rows = [mk("calib", True, 0.9)] * 8 + [mk("calib", False, 0.4)] * 4 + [mk("eval", True, 0.9)] * 5 + [mk("eval", False, 0.95), mk("eval", True, 0.3)]
    t = bo.calib_threshold(rows)
    assert t["reachable"] and 0.4 < t["threshold"] <= 0.9 and t["n_calib"] == 12 and t["n_eval"] == 7
    assert t["eval_n_auto"] == 6 and t["eval_auto_accuracy"] == pytest.approx(5 / 6)
