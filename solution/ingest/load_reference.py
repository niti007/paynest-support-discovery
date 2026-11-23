"""Load reference data (customers, partner map, agents, statutory rules) from the data room. Idempotent upserts.

Tickets are NOT loaded here (they enter via replay + pii_gate). Only the partner rows' company names
and tenant ids are read from tickets_export.json, to build partner_customer_map. No ticket text is read.

Partner map rule: EXACT match of partner end_customer to customers.name. Never substring
(C-1004 Falcon Trading LLC vs C-1050 Falcon Contracting LLC). A name that does not match exactly is
counted as unmatched and not inserted (a human adds it as match_type='manual').
Agent emails/phones are not loaded (the roster has none); names are synthetic.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from solution.a7.db import connect

ROOT = Path(__file__).resolve().parents[2]
DR = ROOT / "data-room"


def _csv(rel):
    with open(DR / rel, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_reference(conn) -> dict:
    customers = _csv("data/customers.csv")
    for r in customers:
        conn.execute(
            "INSERT INTO customers(customer_id,name,tier,region,monthly_fee_usd,employees_paid) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (customer_id) DO UPDATE SET name=EXCLUDED.name,tier=EXCLUDED.tier,region=EXCLUDED.region,"
            "monthly_fee_usd=EXCLUDED.monthly_fee_usd,employees_paid=EXCLUDED.employees_paid",
            (r["customer_id"], r["name"], r["tier"], r["region"], r["monthly_fee_usd"], int(r["employees_paid"])))

    agents = _csv("data/agent_roster.csv")
    for r in agents:
        conn.execute(
            "INSERT INTO agents(agent_id,agent_name,tier,desk,languages,hire_date,status,left_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (agent_id) DO UPDATE SET agent_name=EXCLUDED.agent_name,tier=EXCLUDED.tier,desk=EXCLUDED.desk,"
            "languages=EXCLUDED.languages,hire_date=EXCLUDED.hire_date,status=EXCLUDED.status,left_date=EXCLUDED.left_date",
            (r["agent_id"], r["name"], r["tier"], r["desk"], r["languages"].split(","),
             r["hire_date"] or None, r["status"], r["left_date"] or None))

    with open(DR / "integrations/statutory_rules.json", encoding="utf-8") as f:
        rules = json.load(f)["rules"]
    for r in rules:
        conn.execute(
            "INSERT INTO statutory_rules(rule_id,region,topic,title,effective_from,old_value,new_value,affects_kb_topics,source,status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (rule_id) DO UPDATE SET region=EXCLUDED.region,topic=EXCLUDED.topic,"
            "title=EXCLUDED.title,effective_from=EXCLUDED.effective_from,old_value=EXCLUDED.old_value,new_value=EXCLUDED.new_value,"
            "affects_kb_topics=EXCLUDED.affects_kb_topics,source=EXCLUDED.source,status=EXCLUDED.status",
            (r["rule_id"], r["region"], r["topic"], r["title"], r["effective_from"], r["old_value"], r["new_value"],
             r["affects_kb_topics"], r["source"], r["status"]))

    # Partner map: exact canonical-name match.
    by_name = {c["name"]: c["customer_id"] for c in customers}
    with open(DR / "data/tickets_export.json", encoding="utf-8") as f:
        partner = [t for t in json.load(f)["tickets"] if "end_customer" in t]
    pids: dict[str, set] = defaultdict(set)
    for t in partner:
        pids[t["end_customer"]].add(t["partner_id"])
    matched = {n for n in pids if n in by_name}
    unmatched = sorted(set(pids) - matched)
    # sanity: when a partner row carries a tenant, it must agree with the exact name match
    conflicts = sum(1 for t in partner if t["tenant"] and by_name.get(t["end_customer"]) not in (None, t["tenant"]))
    for n in matched:
        conn.execute(
            "INSERT INTO partner_customer_map(partner_name,customer_id,match_type,confidence,partner_ids) VALUES (%s,%s,'exact',1.00,%s) "
            "ON CONFLICT (partner_name) DO UPDATE SET customer_id=EXCLUDED.customer_id,match_type=EXCLUDED.match_type,"
            "confidence=EXCLUDED.confidence,partner_ids=EXCLUDED.partner_ids",
            (n, by_name[n], sorted(pids[n])))
    null_rows = [t for t in partner if t["tenant"] is None]
    stats = {
        "customers": len(customers), "agents": len(agents), "statutory_rules": len(rules),
        "partner_rows": len(partner), "partner_distinct_names": len(pids),
        "partner_map_matched": len(matched), "partner_map_unmatched": len(unmatched),
        "partner_null_tenant_rows": len(null_rows),
        "partner_null_tenant_ids": len({t["reference"] for t in null_rows}),
        "null_tenant_resolved_by_name": sum(1 for t in null_rows if t["end_customer"] in matched),
        "tenant_vs_name_conflicts": conflicts,
    }
    conn.commit()
    return stats


def main() -> None:
    with connect() as conn:
        stats = load_reference(conn)
        for k, v in stats.items():
            print(f"{k}: {v}")
        for t in ("customers", "partner_customer_map", "agents", "statutory_rules"):
            print(f"db.{t}:", conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0])


if __name__ == "__main__":
    sys.exit(main())
