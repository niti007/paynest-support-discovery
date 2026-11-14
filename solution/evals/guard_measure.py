"""One-shot measurement of the output guard (code rules + LLM stale-claim check) on held-out draft sets, with the REAL rule chunks.

    uv run python -m solution.evals.guard_measure h3|h1|s41|real  [--no-llm]  [--out file.json]

Sets (all drafts labelled by an independent validator, never used to tune):
  h3   solution/tests/data/h3_cases.json   64 wrong / 55 genuine (round 3, real DB chunks)
  h1   solution/tests/data/h1_cases.json   57 wrong / 27 genuine (round 2)
  s41  solution/tests/test_stale_values.py 41 wrong / 11 genuine (the Gate 3/4 re-validation)
Each case goes through the real `make_output_guard` node (decide() stubbed to "yes": it is not what is measured and Nova has a 10 RPM
quota). Verdicts are split into: RULES = a code failure that is a stale / grounding failure (numbers_not_grounded), LLM = stale_claim_llm,
COMBINED = either. For h3 the validator's own definition is also reported: ANY guard failure (citation, date, language ...).
Resumable: results are written after every case. Every LLM call is booked in the spend ledger (component stale_llm).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

import psycopg

from solution.a7 import db
from solution.a7.decide.base import Decision
from solution.a7.nodes import stale_llm
from solution.a7.nodes.output_guard import code_checks, make_output_guard

TESTS = Path(__file__).resolve().parents[1] / "tests"
RID = {"pf": "R-IN-PF-2026-08", "wps": "R-UAE-WPS-2026-07", "gr": "R-UAE-GRAT-2026-01"}
ASOF = {"pf": "2026-09-10", "wps": "2026-08-05", "gr": "2026-03-05"}
REG = {"pf": "IN", "wps": "UAE", "gr": "UAE"}
TICKET = {("pf", "en"): "Our CA says the PF figures differ. What is the current EPF wage ceiling for our payroll?",
          ("pf", "hinglish"): "Hamare PF figures CA se match nahi ho rahe. EPF wage ceiling abhi kitni hai?",
          ("pf", "hi"): "हमारे PF के आंकड़े CA से अलग आ रहे हैं। EPF वेतन सीमा अभी कितनी है?",
          ("wps", "en"): "Our WPS SIF upload was rejected with E104. What format should the file have?",
          ("wps", "hinglish"): "Hamari WPS SIF file reject ho gayi, E104 aa raha hai. File ka format kya hona chahiye?",
          ("wps", "hi"): "हमारी WPS SIF फ़ाइल E104 के साथ अस्वीकृत हुई। फ़ाइल का प्रारूप क्या होना चाहिए?",
          ("wps", "ar"): "تم رفض ملف SIF الخاص بنا برمز E104. ما هو التنسيق المطلوب؟",
          ("gr", "en"): "How is end-of-service gratuity calculated for our part-time staff?"}


class YesDecider:
    name = "stub"

    def decide(self, question, options, *, context=None):
        return Decision("yes", 1.0, "stub", 0.0)


def load_cases(name: str) -> list[dict]:
    if name == "h3":
        return json.loads((TESTS / "data" / "h3_cases.json").read_text())
    if name == "h1":
        d = json.loads((TESTS / "data" / "h1_cases.json").read_text())
        return [{"id": f"{lab[0].upper()}1-{i:02d}", "label": "wrong" if lab == "wrong" else "genuine", "lang": c["lang"], "rule": c["topic"],
                 "draft": c["text"], "ticket": None, "tags": []} for lab in ("wrong", "genuine") for i, c in enumerate(d[lab])]
    if name == "s41":
        from solution.tests.test_stale_values import P, T, W
        return [{"id": f"S{i:02d}", "label": "genuine" if ok else "wrong", "lang": "en", "rule": "wps" if rid == W else "pf", "draft": d,
                 "ticket": None, "tags": []} for i, (d, rid, _stale, ok) in enumerate(T)]
    raise SystemExit(f"unknown set {name}")


def chunk(conn, rid: str) -> dict:
    title, body, eff = conn.execute("select title,body,effective_from from kb_chunks where source_id=%s", (rid,)).fetchone()
    return {"source_id": rid, "title": title, "body": body, "effective_from": eff.isoformat()}


def state_for(conn, case: dict) -> dict:
    r = case["rule"]
    ch = chunk(conn, RID[r])
    return {"masked_text": case.get("ticket") or TICKET.get((r, case["lang"])) or TICKET[(r, "en")], "language": case["lang"], "category": "statutory", "as_of": ASOF[r],
            "tenant_region": REG[r], "draft": case["draft"], "tool_numbers": [], "topic_rules": [RID[r]], "trace": [], "route_reasons": [],
            "retrieved": [{"source_id": RID[r], "kind": "rule", "effective_from": ch["effective_from"], "effective_to": None, "score": 1.0}],
            "context_chunks": [ch]}


def is_rule_stale(f: str) -> bool:
    return f.startswith("numbers_not_grounded")


def run(name: str, use_llm: bool, out: Path) -> list[dict]:
    from solution.evals import spend

    cases = load_cases(name)
    done = {r["id"]: r for r in json.loads(out.read_text())} if out.exists() else {}
    guard = make_output_guard(YesDecider(), max_retries=0)
    results = list(done.values())
    with psycopg.connect(os.environ["A7_DATABASE_URL"]) as conn:
        for c in cases:
            if c["id"] in done:
                continue
            st = state_for(conn, c)
            rec = {k: c[k] for k in ("id", "label", "lang", "rule", "draft", "tags")}
            rec["rule_fails"] = code_checks(dict(st))
            if use_llm:
                spend.guard()
                g = guard(dict(st))
                ent = next(t for t in g["trace"] if t["node"] == "output_guard")
                sv = ent.get("stale_llm") or {}
                spend.add("stale_llm", sv.get("cost_usd", 0.0), calls=sv.get("attempts", 1))
                rec.update(all_fails=g["guard"]["failures"], llm_flag=bool(sv.get("flag")), llm_error=sv.get("error"), llm_reason=sv.get("reason"),
                           llm_backend=sv.get("backend"), llm_tokens_in=sv.get("tokens_in", 0), llm_tokens_out=sv.get("tokens_out", 0),
                           llm_cost_usd=sv.get("cost_usd", 0.0), llm_latency_s=sv.get("latency_s", 0.0), llm_fallback=sv.get("fallback_used", False))
            results.append(rec)
            out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
            print(c["id"], c["label"], "rules" if any(map(is_rule_stale, rec["rule_fails"])) else "-", "llm" if rec.get("llm_flag") else "-",
                  rec.get("llm_error") or "", flush=True)
    return results


def tally(rows: list[dict], key=is_rule_stale) -> dict:
    """bypass = wrong draft that nothing flagged; false rejection = genuine draft something flagged."""
    out = {}
    for label in ("wrong", "genuine"):
        xs = [r for r in rows if r["label"] == label]
        rules = [any(map(key, r["rule_fails"])) for r in xs]
        llm = [bool(r.get("llm_flag")) for r in xs]
        anyf = [bool([f for f in r.get("all_fails", r["rule_fails"]) if not f.startswith("decide:")]) for r in xs]
        n = len(xs)
        if label == "wrong":
            out[label] = {"n": n, "bypass_rules": sum(not a for a in rules), "bypass_llm": sum(not b for b in llm),
                          "bypass_combined": sum(not (a or b) for a, b in zip(rules, llm)), "bypass_any_failure": sum(not a for a in anyf)}
        else:
            out[label] = {"n": n, "false_rej_rules": sum(rules), "false_rej_llm": sum(llm), "false_rej_combined": sum(a or b for a, b in zip(rules, llm)),
                          "false_rej_any_failure": sum(anyf)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("set", choices=["h3", "h1", "s41"])
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    out = Path(a.out or Path(__file__).resolve().parent / "reports" / f"guard_measure_{a.set}.json")
    rows = run(a.set, not a.no_llm, out)
    print(json.dumps(tally(rows), indent=1))
    lat = [r["llm_latency_s"] for r in rows if "llm_latency_s" in r]
    if lat:
        print("llm latency mean/p95", statistics.mean(lat), sorted(lat)[int(0.95 * len(lat)) - 1], "cost mean", statistics.mean(r["llm_cost_usd"] for r in rows))


if __name__ == "__main__":
    sys.exit(main())
