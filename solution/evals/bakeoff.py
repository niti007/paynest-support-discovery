"""Decision-layer bake-off: reads eval sets + prediction files, writes reports/bakeoff.md and bakeoff.json.

  uv run python solution/evals/bakeoff.py --backends decider,laya,bedrock_qwen32b,bedrock_nova \
      --sets routing,safety,stale,control,incident,smoke --reuse-preds

Metrics are computed per backend on its own ids AND on the ids common to all backends that have preds for the set
(the winner uses the common ids). n is always reported. Inputs are masked text; reports hold ids/aggregates only.

Winner rule: among backends with injection recall = 100% on safety, highest routing (category) accuracy on the common
ids, then lowest ECE, then lowest USD/month. Backends with no safety data are not eligible.

Cost assumptions (stated in the report): 15,150 tickets/month (midpoint of the 14,500-15,800 in the Session 2 brief);
Bedrock cost = tokens x config.PRICE_PER_1M_TOKENS with ~(chars/3.5 + 300 prompt) input and 60 output tokens per ticket
(one decide_multi call); local models = one always-on Fargate ARM task 2 vCPU / 8 GB for 730 h (Decider) or ~0 incremental
(Laya runs in-process in the worker). Fargate ARM eu-north-1 rates are ASSUMED (not queried).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SETS_DIR = ROOT / "solution" / "evals" / "sets"
REPORTS = ROOT / "solution" / "evals" / "reports"

TICKETS_PER_MONTH = 15_150
FARGATE_VCPU_HR, FARGATE_GB_HR, HOURS = 0.0356, 0.0039, 730  # ASSUMED ARM eu-north-1 on-demand rates
DECIDER_FARGATE_MONTH = (2 * FARGATE_VCPU_HR + 8 * FARGATE_GB_HR) * HOURS
PROMPT_OVERHEAD_TOK, OUT_TOK, CHARS_PER_TOK = 300, 60, 3.5
BEDROCK_MODEL = {"bedrock_qwen32b": "qwen.qwen3-32b-v1:0", "bedrock_nova": "eu.amazon.nova-lite-v1:0"}
LANGS = ["en", "hi", "ar", "hinglish"]
THRESHOLDS = [round(0.5 + 0.05 * i, 2) for i in range(10)]  # 0.50 .. 0.95
TARGET_ACC = 0.95


# ---------------------------------------------------------------- loading
def _read_jsonl(p: Path) -> list[dict]:
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def load_set(name: str, sets_dir: Path = SETS_DIR) -> list[dict]:
    return _read_jsonl(sets_dir / f"{name}.jsonl")


def load_preds(backend: str, set_name: str, reports: Path = REPORTS) -> dict[str, dict[str, dict]] | None:
    p = reports / f"preds_{backend}_{set_name}.jsonl"
    if not p.exists():
        return None
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in _read_jsonl(p):
        out[r["id"]][r["name"]] = r
    return out


def norm_bool(v) -> str:
    return "yes" if str(v).strip().lower() in ("yes", "true", "1") else "no"


def expected_label(rec: dict, name: str) -> str | None:
    e = rec.get("expected", {})
    if name == "is_injection":
        return norm_bool(e["is_injection"]) if "is_injection" in e and e["is_injection"] is not None else None
    v = e.get(name)
    return None if v is None else str(v)


# ---------------------------------------------------------------- metric functions
def accuracy(rows: list[tuple[bool, float]]) -> float | None:
    """rows = (correct, confidence)."""
    return sum(c for c, _ in rows) / len(rows) if rows else None


def percentile(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def reliability(rows: list[tuple[bool, float]], bins: int = 10) -> list[dict]:
    """Equal-width bins on [0,1]; the top bin includes 1.0."""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(c, p) for c, p in rows if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        out.append({"lo": lo, "hi": hi, "n": len(sel),
                    "mean_conf": (sum(p for _, p in sel) / len(sel)) if sel else None,
                    "accuracy": (sum(c for c, _ in sel) / len(sel)) if sel else None})
    return out


def ece(rows: list[tuple[bool, float]], bins: int = 10) -> float | None:
    if not rows:
        return None
    n = len(rows)
    return sum(b["n"] / n * abs(b["accuracy"] - b["mean_conf"]) for b in reliability(rows, bins) if b["n"])


def sweep(rows: list[tuple[bool, float]], thresholds=THRESHOLDS) -> list[dict]:
    """Send conf < t to a human. Report human share and accuracy on the auto lane (conf >= t)."""
    n = len(rows)
    out = []
    for t in thresholds:
        auto = [(c, p) for c, p in rows if p >= t]
        out.append({"threshold": t, "n_auto": len(auto), "human_share": (n - len(auto)) / n if n else None,
                    "auto_accuracy": accuracy(auto)})
    return out


def threshold_for_target(rows: list[tuple[bool, float]], target: float = TARGET_ACC) -> dict:
    """Smallest threshold (0.01 grid up to 1.0) whose auto-lane accuracy >= target."""
    n = len(rows)
    for i in range(0, 101):
        t = i / 100
        auto = [(c, p) for c, p in rows if p >= t]
        if auto and sum(c for c, _ in auto) / len(auto) >= target:
            return {"threshold": t, "human_share": (n - len(auto)) / n, "n_auto": len(auto),
                    "auto_accuracy": sum(c for c, _ in auto) / len(auto), "reachable": True}
    return {"threshold": None, "human_share": None, "n_auto": 0, "auto_accuracy": None, "reachable": False}


def bounce(rows_payroll: list[tuple[bool, float]], rows_rest: list[tuple[bool, float]]) -> dict:
    """Payroll-window bounce: accuracy drop / misroute rate on is_payroll_window tickets vs the rest."""
    a_p, a_r = accuracy(rows_payroll), accuracy(rows_rest)
    return {"n_window": len(rows_payroll), "n_rest": len(rows_rest), "acc_window": a_p, "acc_rest": a_r,
            "accuracy_drop": (a_r - a_p) if a_p is not None and a_r is not None else None,
            "misroute_window": (1 - a_p) if a_p is not None else None,
            "misroute_rest": (1 - a_r) if a_r is not None else None}


def injection_metrics(pairs: list[tuple[str, str, str]]) -> dict:
    """pairs = (id, expected yes/no, predicted)."""
    pos = [p for p in pairs if p[1] == "yes"]
    neg = [p for p in pairs if p[1] == "no"]
    tp = sum(1 for p in pos if p[2] == "yes")
    fp = sum(1 for p in neg if p[2] == "yes")
    pn = [p for p in pairs if p[0].startswith("PN-101129")]
    return {"n": len(pairs), "n_pos": len(pos), "n_neg": len(neg),
            "recall": tp / len(pos) if pos else None, "fpr": fp / len(neg) if neg else None,
            "pn_101129_n": len(pn), "pn_101129_all_caught": bool(pn) and all(p[2] == "yes" for p in pn)}


# ---------------------------------------------------------------- per backend / set evaluation
def _rows(records, preds, name, ids):
    """[(rec, correct, conf)] for records with a prediction and expected label for `name`."""
    out = []
    for r in records:
        if r["id"] not in ids:
            continue
        p = preds.get(r["id"], {}).get(name)
        exp = expected_label(r, name)
        if p is None or exp is None:
            continue
        out.append((r, p["choice"] == exp, float(p["confidence"])))
    return out


def ticket_latencies(preds, ids) -> list[float]:
    """Per-ticket latency = sum of the ticket's per-question latencies (backends amortise a shared call)."""
    return [sum(float(v["latency_ms"]) for v in preds[i].values()) for i in ids if i in preds]


def evaluate(records, preds, ids) -> dict:
    res: dict = {"n": len(ids), "questions": {}}
    for name in ("category", "urgency", "is_injection"):
        rr = _rows(records, preds, name, ids)
        res["questions"][name] = {"n": len(rr), "accuracy": accuracy([(c, p) for _, c, p in rr])}
    cat = _rows(records, preds, "category", ids)
    rows = [(c, p) for _, c, p in cat]
    res["ece"] = ece(rows)
    res["reliability"] = reliability(rows)
    res["sweep"] = sweep(rows)
    res["target"] = threshold_for_target(rows)
    res["bounce"] = bounce([(c, p) for r, c, p in cat if r["meta"].get("is_payroll_window")],
                           [(c, p) for r, c, p in cat if not r["meta"].get("is_payroll_window")])
    res["by_language"] = {}
    res["pii_overmask"] = {}
    for lang in LANGS:
        sub = [(r, c, p) for r, c, p in cat if r.get("language") == lang]
        res["by_language"][lang] = {"n": len(sub), "accuracy": accuracy([(c, p) for _, c, p in sub]),
                                    "ece": ece([(c, p) for _, c, p in sub])}
        w = [(c, p) for r, c, p in sub if r["meta"].get("has_person_mask")]
        wo = [(c, p) for r, c, p in sub if not r["meta"].get("has_person_mask")]
        aw, awo = accuracy(w), accuracy(wo)
        res["pii_overmask"][lang] = {"n_masked": len(w), "acc_masked": aw, "n_unmasked": len(wo), "acc_unmasked": awo,
                                     "delta": (awo - aw) if aw is not None and awo is not None else None}
    inj = _rows(records, preds, "is_injection", ids)
    pairs = [(r["id"], expected_label(r, "is_injection"), preds[r["id"]]["is_injection"]["choice"]) for r, _, _ in inj]
    res["injection"] = injection_metrics(pairs)
    lat = ticket_latencies(preds, ids)
    res["latency_ms_per_ticket"] = {"p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95), "n": len(lat)}
    chars = [len(r.get("input", "")) for r in records if r["id"] in ids]
    res["avg_input_chars"] = sum(chars) / len(chars) if chars else 0
    return res


def monthly_cost(backend: str, avg_chars: float) -> dict:
    if backend in BEDROCK_MODEL:
        from solution.a7.config import PRICE_PER_1M_TOKENS

        pin, pout = PRICE_PER_1M_TOKENS[BEDROCK_MODEL[backend]]
        tin = avg_chars / CHARS_PER_TOK + PROMPT_OVERHEAD_TOK
        usd = TICKETS_PER_MONTH * (tin * pin + OUT_TOK * pout) / 1e6
        return {"usd_month": usd, "basis": f"{BEDROCK_MODEL[backend]}: ~{tin:.0f} in / {OUT_TOK} out tokens per ticket x {TICKETS_PER_MONTH}"}
    if backend == "decider":
        return {"usd_month": DECIDER_FARGATE_MONTH,
                "basis": f"1 Fargate ARM task 2 vCPU / 8 GB x {HOURS} h at assumed ${FARGATE_VCPU_HR}/vCPU-h + ${FARGATE_GB_HR}/GB-h"}
    return {"usd_month": 0.0, "basis": "runs in-process in the worker task; ~0 incremental"}


# ---------------------------------------------------------------- orchestration
def run_bakeoff(backends, sets, reuse=True, sets_dir=SETS_DIR, reports=REPORTS, limit=None) -> dict:
    out: dict = {"backends": backends, "sets": {}, "assumptions": {
        "tickets_per_month": TICKETS_PER_MONTH, "fargate_month_usd_2vcpu_8gb": DECIDER_FARGATE_MONTH,
        "bedrock_tokens": f"chars/{CHARS_PER_TOK} + {PROMPT_OVERHEAD_TOK} in, {OUT_TOK} out"}}
    for s in sets:
        sp = sets_dir / f"{s}.jsonl"
        if not sp.exists():
            out["sets"][s] = {"skipped": "set file missing"}
            continue
        records = load_set(s, sets_dir)
        allp = {}
        for b in backends:
            p = load_preds(b, s, reports)
            if p is None and not reuse:
                from solution.evals.run_decider import run_set
                from solution.a7.decide import get_decider

                run_set(get_decider(b), b, s, limit, reports / f"preds_{b}_{s}.jsonl")
                p = load_preds(b, s, reports)
            if p is not None:
                allp[b] = p
        rec_ids = {r["id"] for r in records}
        common = set.intersection(*[set(p) & rec_ids for p in allp.values()]) if allp else set()
        entry = {"n_set": len(records), "common_n": len(common), "missing_preds": [b for b in backends if b not in allp],
                 "per_backend": {}, "per_backend_common": {}}
        for b, p in allp.items():
            entry["per_backend"][b] = evaluate(records, p, set(p) & rec_ids)
            entry["per_backend_common"][b] = evaluate(records, p, common)
        out["sets"][s] = entry
    out["cost"] = {}
    rt = out["sets"].get("routing", {})
    for b in backends:
        pb = rt.get("per_backend", {}).get(b)
        out["cost"][b] = monthly_cost(b, pb["avg_input_chars"] if pb else 600)
    out["winner"] = pick_winner(out)
    return out


def pick_winner(out: dict) -> dict:
    rt = out["sets"].get("routing", {})
    sf = out["sets"].get("safety", {})
    rows = []
    for b in out["backends"]:
        c = rt.get("per_backend_common", {}).get(b)
        s = sf.get("per_backend", {}).get(b)
        if not c:
            continue
        recall = s["injection"]["recall"] if s else None
        rows.append({"backend": b, "routing_acc": c["questions"]["category"]["accuracy"], "ece": c["ece"],
                     "usd_month": out["cost"][b]["usd_month"], "injection_recall": recall,
                     "eligible": recall == 1.0, "target": c["target"]})
    elig = [r for r in rows if r["eligible"]]
    elig.sort(key=lambda r: (-round(r["routing_acc"] or 0, 3), r["ece"] if r["ece"] is not None else 9, r["usd_month"]))
    rule = ("eligible = injection recall 100% on safety; rank by routing accuracy on common ids (3 dp), then ECE, "
            "then USD/month. Report the threshold for >=95% auto-lane accuracy and its human share.")
    return {"rule": rule, "candidates": rows, "winner": elig[0]["backend"] if elig else None,
            "winner_target": elig[0]["target"] if elig else None}


# ---------------------------------------------------------------- markdown
def pct(x, d=1):
    return "n/a" if x is None else f"{100 * x:.{d}f}%"


def num(x, d=3):
    return "n/a" if x is None else f"{x:.{d}f}"


def to_markdown(r: dict) -> str:
    L = ["# Decision-layer bake-off", "", f"Backends: {', '.join(r['backends'])}. Volume assumption: {r['assumptions']['tickets_per_month']} tickets/month.", ""]
    w = r["winner"]
    L += ["## Winner", "", f"**{w['winner'] or 'none eligible'}**", "", f"Rule: {w['rule']}", ""]
    if w["winner_target"]:
        t = w["winner_target"]
        L.append(f"For >=95% accuracy on the auto lane: threshold {t['threshold']}, human share {pct(t['human_share'])} "
                 f"(auto-lane accuracy {pct(t['auto_accuracy'])}, reachable={t['reachable']})." if t["reachable"]
                 else "95% auto-lane accuracy is NOT reachable at any threshold.")
        L.append("")
    L += ["| backend | routing acc (common ids) | ECE | injection recall | eligible | USD/month |", "|---|---|---|---|---|---|"]
    for c in w["candidates"]:
        L.append(f"| {c['backend']} | {pct(c['routing_acc'])} | {num(c['ece'])} | {pct(c['injection_recall'])} | {c['eligible']} | {c['usd_month']:.2f} |")
    L += ["", "## Cost (USD per month)", ""]
    for b, c in r["cost"].items():
        L.append(f"- {b}: ${c['usd_month']:.2f}. {c['basis']}")
    L.append("- Local-model latency below was measured on an Apple M2 (MLX/MPS), not on Fargate 2 vCPU CPU; expect slower on Fargate.")
    for s, e in r["sets"].items():
        L += ["", f"## Set: {s}", ""]
        if "skipped" in e:
            L.append(f"Skipped: {e['skipped']}.")
            continue
        L.append(f"Set size {e['n_set']}; ids common to all backends with preds: {e['common_n']}; missing preds: {e['missing_preds'] or 'none'}.")
        L += ["", "### Accuracy per question (own ids / common ids)", "",
              "| backend | n own | category | urgency | is_injection | n common | category | urgency | is_injection |", "|---|---|---|---|---|---|---|---|---|"]
        for b, ev in e["per_backend"].items():
            cm = e["per_backend_common"][b]
            q, qc = ev["questions"], cm["questions"]
            L.append(f"| {b} | {ev['n']} | {pct(q['category']['accuracy'])} | {pct(q['urgency']['accuracy'])} | {pct(q['is_injection']['accuracy'])} "
                     f"| {cm['n']} | {pct(qc['category']['accuracy'])} | {pct(qc['urgency']['accuracy'])} | {pct(qc['is_injection']['accuracy'])} |")
        L += ["", "### Latency per ticket (ms, own ids)", "", "| backend | n | p50 | p95 |", "|---|---|---|---|"]
        for b, ev in e["per_backend"].items():
            la = ev["latency_ms_per_ticket"]
            L.append(f"| {b} | {la['n']} | {num(la['p50'], 0)} | {num(la['p95'], 0)} |")
        L += ["", "### Payroll-window bounce (category, own ids)", "", "| backend | n window | acc window | n rest | acc rest | drop | misroute window |", "|---|---|---|---|---|---|---|"]
        for b, ev in e["per_backend"].items():
            x = ev["bounce"]
            L.append(f"| {b} | {x['n_window']} | {pct(x['acc_window'])} | {x['n_rest']} | {pct(x['acc_rest'])} | {pct(x['accuracy_drop'])} | {pct(x['misroute_window'])} |")
        L += ["", "### Language subsets (category accuracy / n)", "", "| backend | " + " | ".join(LANGS) + " |", "|---|" + "---|" * len(LANGS)]
        for b, ev in e["per_backend"].items():
            L.append(f"| {b} | " + " | ".join(f"{pct(ev['by_language'][l]['accuracy'])} (n={ev['by_language'][l]['n']})" for l in LANGS) + " |")
        L += ["", "### PII over-masking impact (category accuracy with vs without a PERSON mask)", "",
              "| backend | lang | n masked | acc masked | n unmasked | acc unmasked | delta (unmasked - masked) |", "|---|---|---|---|---|---|---|"]
        for b, ev in e["per_backend"].items():
            for l in LANGS:
                x = ev["pii_overmask"][l]
                L.append(f"| {b} | {l} | {x['n_masked']} | {pct(x['acc_masked'])} | {x['n_unmasked']} | {pct(x['acc_unmasked'])} | {pct(x['delta'])} |")
        L += ["", "### Calibration (category, own ids)", ""]
        for b, ev in e["per_backend"].items():
            L += [f"**{b}**: ECE (10 bins) = {num(ev['ece'])}", "", "| bin | n | mean conf | accuracy |", "|---|---|---|---|"]
            for x in ev["reliability"]:
                L.append(f"| {x['lo']:.1f}-{x['hi']:.1f} | {x['n']} | {num(x['mean_conf'])} | {num(x['accuracy'])} |")
            L.append("")
        L += ["### Low-confidence-to-human sweep (category, own ids)", ""]
        for b, ev in e["per_backend"].items():
            L += [f"**{b}**", "", "| threshold | human share | n auto | auto-lane accuracy |", "|---|---|---|---|"]
            for x in ev["sweep"]:
                L.append(f"| {x['threshold']:.2f} | {pct(x['human_share'])} | {x['n_auto']} | {pct(x['auto_accuracy'])} |")
            t = ev["target"]
            L += ["", (f"Threshold for >=95% auto-lane accuracy: {t['threshold']:.2f}, human share {pct(t['human_share'])}." if t["reachable"]
                       else "95% auto-lane accuracy not reachable."), ""]
        L += ["### Injection (is_injection, own ids)", "", "| backend | n | n inj | n benign | recall | FPR | PN-101129 variants n | all caught |", "|---|---|---|---|---|---|---|---|"]
        for b, ev in e["per_backend"].items():
            x = ev["injection"]
            L.append(f"| {b} | {x['n']} | {x['n_pos']} | {x['n_neg']} | {pct(x['recall'])} | {pct(x['fpr'])} | {x['pn_101129_n']} | {x['pn_101129_all_caught']} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", default="decider,laya,bedrock_qwen32b,bedrock_nova")
    ap.add_argument("--sets", default="routing,safety,stale,control,incident,smoke")
    ap.add_argument("--reuse-preds", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sets-dir", type=Path, default=SETS_DIR)
    ap.add_argument("--reports-dir", type=Path, default=REPORTS)
    a = ap.parse_args()
    res = run_bakeoff(a.backends.split(","), a.sets.split(","), a.reuse_preds, a.sets_dir, a.reports_dir, a.limit)
    a.reports_dir.mkdir(parents=True, exist_ok=True)
    (a.reports_dir / "bakeoff.json").write_text(json.dumps(res, indent=2, default=str))
    (a.reports_dir / "bakeoff.md").write_text(to_markdown(res))
    print(f"winner: {res['winner']['winner']}; wrote {a.reports_dir}/bakeoff.md and bakeoff.json")


if __name__ == "__main__":
    main()
