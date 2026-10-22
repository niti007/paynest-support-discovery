#!/usr/bin/env python3
"""Trainer-only: recompute the proof numbers in ANSWER_KEY.md from the raw data room.

    python3 trainer/analysis.py

Standard library only. Reads data-room/ files exactly as a learner would; nothing is imported from the generator.
"""
import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DR = os.path.join(ROOT, "data-room")
IST = timezone(timedelta(hours=5, minutes=30))
GST = timezone(timedelta(hours=4))


def load(rel):
    with open(os.path.join(DR, rel), encoding="utf-8") as f:
        return json.load(f)


def rows(rel):
    with open(os.path.join(DR, rel), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- normalisation helpers
def parse_ts(x):
    """All source formats -> aware datetime in IST. Naive values are assumed to be IST (documented guess)."""
    if x is None or x == "":
        return None
    if isinstance(x, (int, float)):
        return datetime.fromtimestamp(x / 1000.0, tz=timezone.utc).astimezone(IST)
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}) (IST|GST)$", x)
    if m:
        d, mo, y, h, mi, z = m.groups()
        return datetime(int(y), int(mo), int(d), int(h), int(mi), tzinfo=IST if z == "IST" else GST).astimezone(IST)
    s = x.replace("Z", "+00:00").replace(" ", "T")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def canon(label):
    s = re.sub(r"[^a-z]", "", (label or "").lower())
    for key, canon_ in (("payroll", "payroll"), ("statutory", "statutory"), ("compliance", "statutory"), ("how", "howto"),
                        ("report", "reports"), ("leave", "leave"), ("integration", "integration"),
                        ("billing", "billing"), ("access", "access"), ("login", "access"), ("onboarding", "onboarding"),
                        ("newcustomer", "onboarding")):
        if key in s:
            return canon_
    return "unknown:" + s


def normalise(rec):
    """Three export schemas -> one dict."""
    if "ticket_id" in rec:      # email
        evs = [dict(type=e["event"].lower(), at=parse_ts(e["ts"]), by=e["actor"], d=e.get("meta", {})) for e in rec["events"]]
        return dict(id=rec["ticket_id"], channel="email", cust=rec["customer_id"], text=rec["subject"] + " " + rec["body"],
                    cat=rec["category"], created=parse_ts(rec["received_at"]), fr=parse_ts(rec["first_response_at"]),
                    resolved=parse_ts(rec["resolved_at"]), events=evs)
    if "tkt" in rec:            # widget
        evs = [dict(type=e["action"].lower(), at=parse_ts(e["time"]), by=e["who"], d=e.get("data", {})) for e in rec["history"]]
        return dict(id=rec["tkt"], channel="widget", cust=rec["org_id"], text=rec["title"] + " " + rec["message"],
                    cat=rec["cat"], created=parse_ts(rec["created"]), fr=parse_ts(rec["first_reply_ts"]),
                    resolved=parse_ts(rec["closed_ts"]), events=evs)
    evs = [dict(type=e["evt"].lower(), at=parse_ts(e["when"]), by=e["user"], d=e.get("info", {})) for e in rec["activity_log"]]
    return dict(id=rec["reference"], channel="partner", cust=rec.get("tenant"), cust_name=rec["end_customer"],
                text=rec["summary"] + " " + rec["details"], cat=rec["topic"], created=parse_ts(rec["opened_on"]),
                fr=parse_ts(rec["fr_at"]), resolved=parse_ts(rec["closed_at"]), events=evs)


def is_window(dt):
    return dt.day >= 25 or dt.day <= 3


PII = {
    "aadhaar_like": re.compile(r"\b\d{4} \d{4} \d{4}\b(?! \d)"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "emirates_id": re.compile(r"\b784-\d{4}-\d{7}-\d\b"),
    "iban": re.compile(r"\bAE\d{2} \d{4} \d{4} \d{4} \d{4} \d{3}\b"),
    "ifsc_account": re.compile(r"A/C \d{11}, IFSC [A-Z]{4}0\d{6}"),
}


def pct(a, b):
    return "%.1f%%" % (100.0 * a / b) if b else "n/a"


def main():
    raw = load("data/tickets_export.json")["tickets"]
    kb = {a["id"]: a for a in load("data/kb_articles.json")}
    rules = load("integrations/statutory_rules.json")["rules"]
    breaches = rows("data/sla_breaches_q3.csv")
    csat = rows("data/csat_survey.csv")
    jira = load("data/jira_escalations.json")
    custs = {c["customer_id"]: c for c in rows("data/customers.csv")}
    wa = load("data/whatsapp_export.json")
    phone = load("data/call_center_notes.json")

    # ---- 1. dedupe
    seen, tickets, exact_dups = {}, [], 0
    for r in raw:
        n = normalise(r)
        if n["id"] in seen:
            exact_dups += 1
            continue
        seen[n["id"]] = n
        tickets.append(n)
    resub = [t for t in tickets if re.search(r"duplicate of PN-", str([e["d"] for e in t["events"]]))]
    print("=== 1. VOLUME AND DATA QUALITY ===")
    print("export rows: %d | exact re-exported ids: %d | unique ids: %d | closed as duplicate of another ticket: %d"
          % (len(raw), exact_dups, len(tickets), len(resub)))
    print("channels (export, unique):", dict(Counter(t["channel"] for t in tickets)))
    allq3 = len(tickets) - len(resub) + len(wa) + len(phone)
    print("whatsapp threads: %d | phone notes: %d | total distinct customer problems across channels: %d" % (len(wa), len(phone), allq3))
    real = [t for t in tickets if t not in resub]
    allmap = Counter()
    for t in real:
        allmap[canon(t["cat"])] += 1
    print("raw category labels in export:", len(set(t["cat"] for t in real)), "distinct strings ->",
          len(allmap), "canonical categories")
    print("canonical category counts (export):", dict(allmap.most_common()))
    print("first_response_at null:", sum(1 for t in real if t["fr"] is None), "of", len(real))
    win = sum(1 for t in real if is_window(t["created"]))
    print("payroll-window (25th-3rd, IST date) share of export tickets: %d/%d = %s" % (win, len(real), pct(win, len(real))))
    allwin = win + 0
    pii = Counter()
    for t in real:
        for k, rx in PII.items():
            if rx.search(t["text"]):
                pii[k] += 1
    print("PII-looking strings in export bodies (tickets):", dict(pii))
    print("tickets with at least one PII pattern: %d of %d" % (sum(1 for t in real if any(rx.search(t["text"]) for rx in PII.values())), len(real)))
    inj = [t["id"] for t in real if re.search(r"ignore (all )?previous instructions", t["text"], re.I)]
    print("prompt-injection ticket id(s):", inj)

    # ---- 2. misroute + stale
    print("\n=== 2. MISROUTED AND STALE-KB TICKETS ===")
    stale_rules = []
    for ru in rules:
        for a in kb.values():
            if a["topic"] in ru["affects_kb_topics"] and a["last_reviewed"] < ru["effective_from"] and ru["effective_from"] <= "2026-09-30":
                stale_rules.append((a["id"], ru["rule_id"], ru["effective_from"]))
    print("KB articles last reviewed BEFORE a rule that affects their topic:", sorted(stale_rules))
    stale_idx = defaultdict(list)
    for aid, rid, eff in stale_rules:
        stale_idx[aid].append(eff)
    old_unaffected = [a["id"] for a in kb.values() if a["last_reviewed"] < "2024-01-01"]
    print("(red herring) KB articles last reviewed in 2023 that no rule affects:", old_unaffected)

    for t in real:
        reass = [e for e in t["events"] if e["type"] == "reassigned"]
        created_ev = [e for e in t["events"] if e["type"] == "created"][0]
        t["n_reassign"] = len(reass)
        t["init_cat"] = canon(created_ev["d"].get("category"))
        t["final_cat"] = canon(t["cat"])
        t["misrouted"] = t["n_reassign"] >= 2 and t["init_cat"] != t["final_cat"]
        t["window"] = is_window(t["created"])
        t["stale"] = False
        t["stale_kb"] = None
        for e in t["events"]:
            if e["type"] == "replied" and e["d"].get("tier") == "L1" and e["d"].get("kb_article_id") in stale_idx:
                eff = datetime.strptime(min(stale_idx[e["d"]["kb_article_id"]]), "%Y-%m-%d").replace(tzinfo=IST)
                if e["at"] >= eff:
                    t["stale"] = True
                    t["stale_kb"] = e["d"]["kb_article_id"]
        t["reopened"] = any(e["type"] == "reopened" for e in t["events"])
        t["jira"] = [e["d"].get("jira_key") for e in t["events"] if e["type"] == "escalated" and e["d"].get("jira_key")]
    byid = {t["id"]: t for t in real}
    mis = [t for t in real if t["misrouted"]]
    stl = [t for t in real if t["stale"]]
    print("misrouted (reassigned >=2x AND initial category != final): %d  (in payroll window: %s)"
          % (len(mis), pct(sum(1 for t in mis if t["window"]), len(mis))))
    print("L1 reply citing KB stale at reply time: %d  (in payroll window: %s)"
          % (len(stl), pct(sum(1 for t in stl if t["window"]), len(stl))))
    base = [t for t in real if not t["stale"]]
    print("reopen rate: stale-KB tickets %s | all others %s"
          % (pct(sum(t["reopened"] for t in stl), len(stl)), pct(sum(t["reopened"] for t in base), len(base))))
    pre = [t for t in real if t["stale_kb"] is None and any(
        e["type"] == "replied" and e["d"].get("kb_article_id") in stale_idx for e in t["events"])]
    print("control: tickets citing the same KB articles BEFORE the rule date (not stale): %d, reopen %s"
          % (len(pre), pct(sum(t["reopened"] for t in pre), len(pre))))
    dec = [t for t in real if t["n_reassign"] >= 2 and not t["misrouted"]]
    print("decoy: reassigned >=2x but category was right (shift handover etc.): %d" % len(dec))
    dec1 = [t for t in real if t["n_reassign"] == 1 and t["init_cat"] != t["final_cat"]]
    print("decoy: wrong category but fixed in one hop: %d" % len(dec1))
    wp = [t for t in real if t["window"] and t["final_cat"] in ("payroll", "statutory")]
    print("window payroll+statutory tickets: %d | misrouted rate %s | vs off-window all-category misrouted rate %s"
          % (len(wp), pct(sum(t["misrouted"] for t in wp), len(wp)),
             pct(sum(t["misrouted"] for t in real if not t["window"]), sum(1 for t in real if not t["window"]))))

    # ---- 3. SLA credits
    print("\n=== 3. SLA CREDITS (sla_breaches_q3.csv) ===")
    tot = 0.0
    cause = defaultdict(float)
    cnt = Counter()
    bycat = defaultdict(float)
    missing = []
    for b in breaches:
        t = byid.get(b["ticket_id"])
        c = float(b["credit_usd"])
        tot += c
        if not t:
            missing.append(b["ticket_id"])
            continue
        bycat[t["final_cat"]] += c
        if t["window"] and t["misrouted"] and t["stale"]:
            k = "window: misrouted AND stale"
        elif t["window"] and t["misrouted"]:
            k = "window: misrouted only"
        elif t["window"] and t["stale"]:
            k = "window: stale-KB only"
        else:
            k = "other"
        cause[k] += c
        cnt[k] += 1
    A = sum(v for k, v in cause.items() if k != "other")
    print("breaches: %d | total credits: $%.2f | breach rows with no matching ticket: %d" % (len(breaches), tot, len(missing)))
    for k in ("window: misrouted only", "window: stale-KB only", "window: misrouted AND stale", "other"):
        print("  %-30s n=%3d  $%10.2f  %s" % (k, cnt[k], cause[k], pct(cause[k], tot)))
    print("ROOT-CAUSE SHARE (payroll-window AND (misrouted OR stale-KB)): $%.2f of $%.2f = %s"
          % (A, tot, pct(A, tot)))
    nA = sum(cnt[k] for k in cnt if k != "other")
    print("  = %d of %d breaches (%s of breach count)" % (nA, len(breaches), pct(nA, len(breaches))))
    print("credits by final category:", {k: "$%.0f (%s)" % (v, pct(v, tot)) for k, v in sorted(bycat.items(), key=lambda kv: -kv[1])})
    hw = [t for t in real if t["final_cat"] == "howto"]
    print("how-to: %s of tickets, %s of credits" % (pct(len(hw), len(real)), pct(bycat["howto"], tot)))
    reasons = Counter(b["agent_noted_reason"] or "(blank)" for b in breaches)
    print("agent_noted_reason (what the desk wrote, not what happened):", dict(reasons.most_common()))
    fee = {b["ticket_id"]: b for b in breaches}
    kes = [b for b in breaches if b["customer_id"] == "C-1001"]
    print("Kestrel (C-1001) share of credits: $%.2f = %s" % (sum(float(b["credit_usd"]) for b in kes), pct(sum(float(b["credit_usd"]) for b in kes), tot)))
    # verify breach hours reconcile with event timeline
    bad = 0
    for b in breaches:
        t = byid.get(b["ticket_id"])
        if t:
            hrs = (max(e["at"] for e in t["events"] if e["type"] == "resolved") - t["created"]).total_seconds() / 3600
            if abs(hrs - float(b["actual_resolution_h"])) > 0.15:
                bad += 1
    print("breach rows whose actual_resolution_h does not reconcile to ticket events: %d" % bad)

    # ---- 4. CSAT red herring
    print("\n=== 4. CSAT ===")
    seen_s, cs = set(), []
    for r in csat:
        if r["survey_id"].rstrip("b") in seen_s:
            continue
        seen_s.add(r["survey_id"].rstrip("b"))
        cs.append(r)
    def mean(xs):
        return sum(xs) / len(xs) if xs else float("nan")
    for label, filt in (("all customers", lambda r: True), ("excluding Kestrel C-1001", lambda r: r["customer_id"] != "C-1001"),
                        ("Kestrel only", lambda r: r["customer_id"] == "C-1001")):
        out = []
        for m in ("2026-07", "2026-08", "2026-09"):
            xs = []
            for r in cs:
                t = byid.get(r["ticket_id"])
                if t and t["resolved"].strftime("%Y-%m") == m and filt(r):
                    xs.append(int(r["score"]))
            out.append("%s %.2f (n=%d)" % (m[-2:], mean(xs), len(xs)))
        print("  %-26s" % label, " | ".join(out))
    kj = [j for j in jira if "C-1001" in j["customer_ids"]]
    print("Kestrel Jira issues:", [(j["key"], len(j["linked_ticket_ids"])) for j in kj])

    # ---- 5. incident spike
    print("\n=== 5. INCIDENT SPIKE ===")
    day = defaultdict(Counter)
    for t in real:
        day[(t["cust"], t["created"].strftime("%Y-%m-%d"))][canon(t["cat"])] += 1
    top = sorted(((sum(v.values()), k) for k, v in day.items() if k[0]), reverse=True)[:3]
    print("largest same-customer same-day ticket bursts:", top)
    inc = [t for t in real if t["cust"] == "C-1002" and t["created"].strftime("%Y-%m-%d") == "2026-09-25" and re.search(r"payslip.*(blank|zero)", t["text"], re.I)]
    ts = sorted(t["created"] for t in inc)
    print("Meridian payslip-PDF tickets on 2026-09-25: %d between %s and %s IST" % (len(inc), ts[0].strftime("%H:%M"), ts[-1].strftime("%H:%M")))
    print("linked Jira:", [j["key"] for j in jira if j["key"] == "PNJ-5107"])

    # ---- 6. languages
    print("\n=== 6. LANGUAGE MIX (export, by script/heuristics) ===")
    deva = sum(1 for t in real if re.search(r"[ऀ-ॿ]", t["text"]))
    arab = sum(1 for t in real if re.search(r"[؀-ۿ]", t["text"]))
    hing = sum(1 for t in real if re.search(r"\b(nahi|hai|karo|kaise|aa raha|dikha|kar diya)\b", t["text"], re.I))
    print("Devanagari: %d | Arabic: %d | Hinglish (roman Hindi markers): %d" % (deva, arab, hing))

    # ---- 7. cost sheet
    print("\n=== 7. COST SHEET ===")
    for r in rows("data/monthly_cost_sheet.csv"):
        print("  ", r["month"], "tickets(all customers)", r["tickets_received_all_customers"], "extract", r["tickets_in_ops_extract"], "cost/ticket excl credits $", r["cost_per_ticket_excl_credits_usd"],
              "incl credits $", r["cost_per_ticket_incl_credits_usd"], "credits $", r["sla_credits_paid_usd"])


if __name__ == "__main__":
    main()
