"""Build the Phase 2 eval sets: solution/evals/sets/*.jsonl

Every input goes through pii_gate then normalise (the same nodes as the graph), so the sets hold only masked text.
Records: {id, source, input, language, expected{category, urgency, is_injection, ...}, meta{...}}.

Usage (repo root): uv run python solution/evals/build_sets.py
"""
from __future__ import annotations

import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE.parent))

from solution.a7.nodes.normalise import CustomerIndex, canon, make_normalise, normalise, parse_ts  # noqa: E402
from solution.a7.nodes.pii_gate import make_pii_gate  # noqa: E402
from solution.a7.vault import LocalKeyProvider, MemoryVaultStore, Vault  # noqa: E402
import safety_templates as st  # noqa: E402

DATA = HERE.parents[2] / "data-room" / "data"
RULES = HERE.parents[2] / "data-room" / "integrations" / "statutory_rules.json"
SETS = HERE.parent / "sets"
SEED = 20261108
CALIB_FRACTION = 0.2

URGENCY = {"P1": "urgent", "P2": "high", "P3": "normal", "urgent": "urgent", "high": "high", "normal": "normal",
           "S1": "urgent", "S2": "high", "S3": "normal"}
SMOKE_IDS = ["PN-100402", "PN-101055", "PN-101333", "PN-101129", "PN-100802", "PN-101504", "PN-100921", "PN-100276",
             "PN-101315", "PN-100724", "PN-100393", "PN-101234", "PN-100270", "PN-100088"]
MERIDIAN = [f"PN-{i}" for i in range(101234, 101271)]  # filtered below by customer C-1002 + subject
IST_MIDNIGHT = "T00:00:00+05:30"


def raw_id(t: dict) -> str:
    return t.get("ticket_id") or t.get("tkt") or t.get("reference")


def raw_priority(t: dict) -> str:
    return t.get("priority") or t.get("prio") or t.get("severity")


def load_real() -> tuple[list[dict], list[dict], list[dict]]:
    """(real tickets, unique tickets, raw rows). Dedupe: first occurrence by id, then drop 'duplicate of PN-' resubmits."""
    rows = json.loads((DATA / "tickets_export.json").read_text())["tickets"]
    seen, uniq = set(), []
    for t in rows:
        if raw_id(t) not in seen:
            seen.add(raw_id(t))
            uniq.append(t)
    resub = {raw_id(t) for t in uniq if re.search(r"duplicate of PN-", json.dumps(t))}
    return [t for t in uniq if raw_id(t) not in resub], uniq, rows


class Pipeline:
    """pii_gate -> normalise, with a throwaway vault key (never persisted)."""

    def __init__(self):
        vault = Vault(MemoryVaultStore(), LocalKeyProvider(master_key=bytes(range(32))))
        self.gate = make_pii_gate(vault)
        self.norm = make_normalise(CustomerIndex.from_csv())

    def run(self, raw: dict) -> dict:
        s = self.gate({"raw_record": raw})
        if s["pii_gate_status"] != "passed":
            raise RuntimeError(f"pii_gate blocked {s.get('ticket_id')}: {s['trace'][-1]}")
        return self.norm(s)


def stale_and_control(real: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """Stale: L1 reply citing a stale KB article at/after the rule's effective date. Control: same articles before it."""
    kb = json.loads((DATA / "kb_articles.json").read_text())
    rules = json.loads(RULES.read_text())["rules"]
    stale_art: dict[str, dict] = {}  # kb id -> rule
    for r in rules:
        if r["effective_from"] > "2026-09-30":
            continue
        for a in kb:
            if a["topic"] in r["affects_kb_topics"] and a["last_reviewed"] < r["effective_from"]:
                stale_art[a["id"]] = r
    stale, control = {}, {}
    for t in real:
        n = normalise(t)
        hit = None
        for e in n["events"]:
            if e["type"] != "replied" or e["d"].get("tier") != "L1":
                continue
            kid = e["d"].get("kb_article_id")
            if kid in stale_art:
                eff = parse_ts(stale_art[kid]["effective_from"] + IST_MIDNIGHT[0:0] + "T00:00:00")
                hit = hit or (kid, stale_art[kid]["rule_id"], e["at"] >= eff, e["at"].isoformat())
                if e["at"] >= eff:
                    hit = (kid, stale_art[kid]["rule_id"], True, e["at"].isoformat())
                    break
        if hit:
            rec = {"kb": hit[0], "rule": hit[1], "reply_at": hit[3]}
            (stale if hit[2] else control)[n["id"]] = rec
    return stale, control


def reassign_info(t: dict) -> dict:
    n = normalise(t)
    evs = [e for e in n["events"] if e["type"] == "reassigned"]
    created = next((e for e in n["events"] if e["type"] == "created"), None)
    initial = canon((created or {}).get("d", {}).get("category") or n["cat"])
    final = canon(n["cat"])
    return {"n_reassigns": len(evs), "initial_category": initial, "final_category": final,
            "misrouted": len(evs) >= 2 and initial != final, "reopened": any(e["type"] == "reopened" for e in n["events"])}


def ticket_record(pipe: Pipeline, t: dict, extra_meta: dict | None = None) -> dict:
    s = pipe.run(t)
    n = normalise(t)
    ra = reassign_info(t)
    has_person = any(k.startswith("<PERSON_") for k in s["pii_placeholders"])
    tid = raw_id(t)
    return {
        "id": tid,
        "source": s["channel"],
        "input": s["masked_text"],
        "language": s["language"],
        "expected": {"category": canon(n["cat"]), "urgency": URGENCY[raw_priority(t)],
                     "is_injection": tid == "PN-101129"},
        "meta": {"is_payroll_window": s["is_payroll_window"], "has_person_mask": has_person,
                 "reassigned": ra["n_reassigns"] >= 1, "n_reassigns": ra["n_reassigns"],
                 "misrouted": ra["misrouted"], "initial_category": ra["initial_category"],
                 "reopened": ra["reopened"], "customer_ref": s["customer_ref"],
                 "pii_types": sorted({typ for ph, typ in s["pii_placeholders"].items() if ph in s["masked_text"]}), **(extra_meta or {})},
    }


def calib_split(records: list[dict], seed: int = SEED, fraction: float = CALIB_FRACTION) -> None:
    """Stratified by category (then spread across languages by systematic sampling). Writes rec['split']."""
    rng = random.Random(seed)
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_cat[r["expected"]["category"]].append(r)
    step = round(1 / fraction)
    for cat in sorted(by_cat):
        grp = by_cat[cat]
        by_lang: dict[str, list[dict]] = defaultdict(list)
        for r in sorted(grp, key=lambda r: r["id"]):
            by_lang[r["language"]].append(r)
        ordered: list[dict] = []
        for lang in sorted(by_lang):
            rng.shuffle(by_lang[lang])
            ordered += by_lang[lang]
        off = rng.randrange(step)
        calib_ids = {r["id"] for i, r in enumerate(ordered) if i % step == off}
        for r in grp:
            r["split"] = "calib" if r["id"] in calib_ids else "eval"


def build_safety(pipe: Pipeline, real_by_id: dict[str, dict]) -> list[dict]:
    rng = random.Random(SEED)
    out = [dict(real_by_id["PN-101129"], source="widget")]
    out[0] = {**out[0], "meta": {**out[0]["meta"], "variant": "original", "style": "override"}}
    combos = [(s, l) for s in st.STYLES for l in st.LANGS]
    plan = combos + combos + combos[:11]  # 59 variants
    seen_variant: Counter = Counter()
    for k, (style, lang) in enumerate(plan):
        v = seen_variant[(style, lang)]
        seen_variant[(style, lang)] += 1
        text, cat = st.make_injection(rng, style, lang, v)
        out.append(synthetic(pipe, f"PN-101129-v{k + 1:02d}", text, cat, True, {"variant": "injection", "style": style, "lang": lang}))
    for k in range(60):
        lang = st.LANGS[k % 4]
        text, cat = st.make_benign(rng, lang, k // 4, k // 20)
        out.append(synthetic(pipe, f"SAFE-BENIGN-{k + 1:02d}", text, cat, False, {"variant": "benign_lookalike", "lang": lang}))
    return out


def synthetic(pipe: Pipeline, sid: str, text: str, category: str, inj: bool, meta: dict) -> dict:
    raw = {"tkt": sid, "source": "inapp_widget", "org_id": "C-1031", "user_ref": "U-10001", "title": "Support request",
           "message": text, "cat": category, "prio": "normal", "state": "closed", "created": "2026-09-12 16:41:07",
           "first_reply_ts": None, "closed_ts": 1789000000000, "history": []}
    s = pipe.run(raw)
    return {"id": sid, "source": "synthetic", "input": s["masked_text"], "language": s["language"],
            "expected": {"category": category, "urgency": "normal", "is_injection": inj},
            "meta": {"is_payroll_window": s["is_payroll_window"], "has_person_mask": any(k.startswith("<PERSON_") for k in s["pii_placeholders"]),
                     "reassigned": False, **meta}}


def write(name: str, recs: list[dict]) -> None:
    SETS.mkdir(parents=True, exist_ok=True)
    with (SETS / f"{name}.jsonl").open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    real, uniq, rows = load_real()
    pipe = Pipeline()
    recs = [ticket_record(pipe, t) for t in real]
    by_id = {r["id"]: r for r in recs}
    raw_by_id = {raw_id(t): t for t in uniq}  # includes resubmits (smoke needs PN-101504)

    calib_split(recs)
    write("routing", recs)
    lead = [r for r in recs if r["meta"]["reassigned"]]
    write("routing_reassigned", lead)

    stale, control = stale_and_control(real)
    write("stale", [{**by_id[i], "meta": {**by_id[i]["meta"], "stale": True, **stale[i]}} for i in sorted(stale)])
    write("control", [{**by_id[i], "meta": {**by_id[i]["meta"], "stale": False, "control": True, **control[i]}} for i in sorted(control)])

    meridian = [r for r in recs if r["meta"]["customer_ref"] == "C-1002" and r["id"] in set(MERIDIAN)
                and re.search(r"payslip|net pay", r["input"], re.I) and r["id"] not in ()]
    jira = json.loads((DATA / "jira_escalations.json").read_text())
    kest = next(j for j in jira if j["key"] == "PNJ-4412")["linked_ticket_ids"]
    inc = [{**r, "meta": {**r["meta"], "incident": "meridian-2026-09-25"}} for r in meridian]
    inc += [{**by_id[i], "meta": {**by_id[i]["meta"], "incident": "PNJ-4412"}} for i in sorted(kest) if i in by_id]
    write("incident", inc)

    smoke = []
    for i in SMOKE_IDS:
        r = by_id.get(i) or ticket_record(pipe, raw_by_id[i], {"duplicate_of": "PN-100802"})
        smoke.append(r)
    write("smoke", smoke)

    write("safety", build_safety(pipe, by_id))

    counts = {p.stem: sum(1 for _ in p.open()) for p in sorted(SETS.glob("*.jsonl"))}
    print(json.dumps(counts, indent=1))
    print("calib/eval:", Counter(r["split"] for r in recs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
