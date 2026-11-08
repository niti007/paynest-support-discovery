"""Preliminary predictions (Builder A). Writes solution/evals/reports/preds_<backend>_<set>.jsonl and prints metrics.

One line per (ticket, question): {id, name, choice, confidence, latency_ms, [probs, raw_confidence, tokens_in, tokens_out, cost_usd]}.
latency/tokens/cost are amortised over the questions answered by one call. is_injection choices are "yes"/"no".
No ticket text. Builder B's bakeoff.py reuses these files.

  uv run python solution/evals/prelim_preds.py laya routing-eval|safety
  uv run python solution/evals/prelim_preds.py qwen3-32b routing|safety
  uv run python solution/evals/prelim_preds.py nova-lite routing-300|safety
  uv run python solution/evals/prelim_preds.py laya-calibrate     # fit T on the calib split, report ECE raw vs calibrated
"""
from __future__ import annotations

import json
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

from solution.a7.decide.base import INVALID, classify_questions, get_decider  # noqa: E402

SETS = HERE.parent / "sets"
REPORTS = HERE.parent / "reports"
SEED = 20261108


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in (SETS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


def select(set_name: str) -> tuple[str, list[dict]]:
    if set_name == "routing-eval":
        return "routing-eval", [r for r in load("routing") if r["split"] == "eval"]
    if set_name == "routing-300":  # stratified by category, fixed seed, eval split only
        rng = random.Random(SEED)
        recs = [r for r in load("routing") if r["split"] == "eval"]
        by = defaultdict(list)
        for r in recs:
            by[r["expected"]["category"]].append(r)
        out = []
        for cat in sorted(by):
            g = sorted(by[cat], key=lambda r: r["id"])
            rng.shuffle(g)
            out += g[: round(300 * len(g) / len(recs))]
        return "routing-300", out[:300]
    return set_name, load(set_name)


def run(dec, recs: list[dict], workers: int) -> list[dict]:
    def one(r):
        items = classify_questions({"masked_text": r["input"]})
        out = dec.decide_multi(items)
        rows = []
        for k, d in out.items():
            n = max(len(out), 1)
            row = {"id": r["id"], "name": k, "choice": d.choice, "confidence": round(d.confidence, 5),
                   "latency_ms": round(d.latency_ms, 2)}
            if "probs" in d.raw:
                row["probs"] = d.raw["probs"]
            if "raw_confidence" in d.raw:
                row["raw_confidence"] = d.raw["raw_confidence"]
            if "tokens_in" in d.raw:
                row.update(tokens_in=d.raw["tokens_in"] / n, tokens_out=d.raw["tokens_out"] / n, cost_usd=d.raw["cost_usd"] / n)
            if "error" in d.raw:
                row["error"] = d.raw["error"]
            rows.append(row)
        return rows
    done = [0]

    def logged(r):
        out = one(r)
        done[0] += 1
        if done[0] % 50 == 0:
            print(f"progress {done[0]}/{len(recs)}", flush=True)
        return out
    if workers <= 1:
        return [row for r in recs for row in logged(r)]
    with ThreadPoolExecutor(workers) as ex:
        return [row for rows in ex.map(logged, recs) for row in rows]


def ece(pairs, bins=10):
    n = len(pairs)
    tot = 0.0
    for b in range(bins):
        sel = [(c, ok) for c, ok in pairs if min(int(c * bins), bins - 1) == b]
        if sel:
            tot += len(sel) / n * abs(sum(ok for _, ok in sel) / len(sel) - sum(c for c, _ in sel) / len(sel))
    return tot


def gold(rec: dict, q: str) -> str:
    v = rec["expected"][q]
    return ("yes" if v else "no") if q == "is_injection" else v


def metrics(preds: list[dict], recs: list[dict]) -> dict:
    exp = {r["id"]: r for r in recs}
    by_q: dict[str, list[dict]] = defaultdict(list)
    for p in preds:
        by_q[p["name"]].append(p)
    m: dict = {"n_tickets": len(exp)}
    for q, ps in by_q.items():
        ok = [(p["confidence"], p["choice"] == gold(exp[p["id"]], q)) for p in ps]
        m[f"{q}_acc"] = round(sum(o for _, o in ok) / len(ok), 4)
        m[f"{q}_invalid"] = sum(1 for p in ps if p["choice"] == INVALID)
        m[f"{q}_ece"] = round(ece(ok), 4)
    inj = by_q["is_injection"]
    pos = [p for p in inj if exp[p["id"]]["expected"]["is_injection"]]
    neg = [p for p in inj if not exp[p["id"]]["expected"]["is_injection"]]
    if pos and neg and len(exp) <= 200:
        m["injection_recall"] = round(sum(p["choice"] == "yes" for p in pos) / len(pos), 4)
        m["injection_missed_ids"] = sorted(p["id"] for p in pos if p["choice"] != "yes")
        m["injection_fpr"] = round(sum(p["choice"] == "yes" for p in neg) / len(neg), 4)
    elif neg:
        m["injection_fpr_on_routing"] = round(sum(p["choice"] == "yes" for p in neg) / len(neg), 4)
    m["pn101129_flagged"] = next((p["choice"] for p in inj if p["id"] == "PN-101129"), None)
    per_ticket: dict[str, float] = defaultdict(float)
    for p in preds:
        per_ticket[p["id"]] += p["latency_ms"]
    lat = sorted(per_ticket.values())
    m["ticket_latency_p50_ms"], m["ticket_latency_p95_ms"] = round(lat[len(lat) // 2], 1), round(lat[int(len(lat) * 0.95)], 1)
    m["cost_usd"] = round(sum(p.get("cost_usd", 0.0) for p in preds), 5)
    m["tokens_in"], m["tokens_out"] = round(sum(p.get("tokens_in", 0) for p in preds)), round(sum(p.get("tokens_out", 0) for p in preds))
    return m


def laya_calibrate() -> dict:
    """Fit temperatures on the CALIB split (routing), then apply to the EVAL-split preds. Writes calib/laya.json."""
    from solution.a7.decide.laya import Calibrator, scale_probs

    routing = load("routing")
    calib = [r for r in routing if r["split"] == "calib"]
    cpreds = run(get_decider("laya"), calib, 1)
    write("laya", "routing-calib", cpreds)
    cexp = {r["id"]: r for r in calib}
    by_q: dict[str, list] = defaultdict(list)
    for p in cpreds:
        if p["name"] in ("category", "urgency"):
            by_q[p["name"]].append((p["probs"], gold(cexp[p["id"]], p["name"])))
    cal = Calibrator.fit(by_q, {"fit_split": "routing calib (20%, stratified, seed 20261108)", "n": len(calib)})
    cal.save()
    epreds = read("laya", "routing-eval")
    exp = {r["id"]: r for r in routing}
    res = {"temperatures": cal.temperatures}
    for q in ("category", "urgency"):
        raw_pairs, cal_pairs = [], []
        for p in (x for x in epreds if x["name"] == q):
            probs = p["probs"]
            ok = p["choice"] == exp[p["id"]]["expected"][q]
            raw_pairs.append((probs.get(p["choice"], 0.0), ok))
            cal_pairs.append((scale_probs(probs, cal.temperature(q)).get(p["choice"], 0.0), ok))
        res[q] = {"ece_raw": round(ece(raw_pairs), 4), "ece_calibrated": round(ece(cal_pairs), 4),
                  "acc": round(sum(o for _, o in raw_pairs) / len(raw_pairs), 4),
                  "mean_conf_raw": round(sum(c for c, _ in raw_pairs) / len(raw_pairs), 4),
                  "mean_conf_cal": round(sum(c for c, _ in cal_pairs) / len(cal_pairs), 4)}
    return res


def write(backend: str, name: str, preds: list[dict]) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"preds_{backend}_{name}.jsonl"
    path.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in preds), encoding="utf-8")
    return path


def read(backend: str, name: str) -> list[dict]:
    return [json.loads(l) for l in (REPORTS / f"preds_{backend}_{name}.jsonl").read_text().splitlines()]


def main(argv: list[str]) -> int:
    backend = argv[1]
    if backend == "laya-calibrate":
        print(json.dumps(laya_calibrate(), indent=1))
        return 0
    name, recs = select(argv[2])
    dec_name, workers = {"laya": ("laya", 1), "bedrock_qwen32b": ("bedrock_qwen32b", 6), "bedrock_nova": ("bedrock_nova", 3)}[backend]
    dec = get_decider(dec_name)
    t0 = time.time()
    preds = run(dec, recs, workers)
    path = write(backend, name, preds)
    m = metrics(preds, recs)
    m["wall_s"] = round(time.time() - t0, 1)
    if hasattr(dec, "usage"):
        m["usage"] = dec.usage()
    print(path.name, json.dumps(m, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
