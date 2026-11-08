"""Needs the loaders to have run (load_reference + load_kb) against the compose postgres."""
import csv
import json
from pathlib import Path

import pytest

from solution.ingest.load_kb import build_chunks, split_text

pytestmark = pytest.mark.db
DR = Path(__file__).resolve().parents[2] / "data-room"


def n(owner, sql):
    return owner.execute(sql).fetchone()[0]


def test_row_counts(owner):
    assert n(owner, "SELECT count(*) FROM customers") == len(list(csv.DictReader(open(DR / "data/customers.csv")))) == 60
    assert n(owner, "SELECT count(*) FROM agents") == 49
    assert n(owner, "SELECT count(*) FROM statutory_rules") == 4
    assert n(owner, "SELECT count(DISTINCT source_id) FROM kb_chunks WHERE source='kb'") == 24
    assert n(owner, "SELECT count(DISTINCT source_id) FROM kb_chunks WHERE source='rule'") == 4


def test_partner_map_counts(owner):
    tickets = json.load(open(DR / "data/tickets_export.json"))["tickets"]
    names = {t["end_customer"] for t in tickets if "end_customer" in t}
    assert len(names) == 35
    assert n(owner, "SELECT count(*) FROM partner_customer_map WHERE match_type='exact'") == 35  # matched
    assert len(names - {r[0] for r in owner.execute("SELECT partner_name FROM partner_customer_map")}) == 0  # unmatched


def test_supersede_links(owner):
    rows = dict((r[0], r[1:]) for r in owner.execute(
        "SELECT source_id, superseded_by, effective_to::text FROM kb_chunks WHERE superseded_by IS NOT NULL"))
    assert rows == {
        "KB-0201": ("R-UAE-WPS-2026-07", "2026-07-14"), "KB-0202": ("R-UAE-WPS-2026-07", "2026-07-14"),
        "KB-0142": ("R-IN-PF-2026-08", "2026-07-31"), "KB-0143": ("R-IN-PF-2026-08", "2026-07-31"),
    }
    rules = dict(owner.execute("SELECT source_id, effective_from::text FROM kb_chunks WHERE source='rule'").fetchall())
    assert rules["R-UAE-WPS-2026-07"] == "2026-07-15" and rules["R-IN-PF-2026-08"] == "2026-08-01"


def test_falcon_exact_only(owner):
    m = dict(owner.execute("SELECT partner_name, customer_id FROM partner_customer_map WHERE partner_name ILIKE '%falcon%'").fetchall())
    assert m == {"Falcon Trading LLC": "C-1004"}
    assert n(owner, "SELECT count(*) FROM partner_customer_map WHERE customer_id='C-1050'") == 0


def test_no_unflagged_pii_columns(owner):
    cols = {r[0] for r in owner.execute("SELECT column_name FROM information_schema.columns WHERE table_name IN ('agents','customers')")}
    assert not {c for c in cols if "email" in c or "phone" in c}


def test_chunker_units():
    chunks, c = build_chunks()
    assert c == {"articles": 24, "rules": 4} and len(chunks) == 28
    long = " ".join(f"Sentence number {i} about payroll." for i in range(400))
    parts = split_text(long)
    assert len(parts) > 1 and all(len(p.split()) * 1.3 <= 560 for p in parts)
    assert parts[1].split()[0] in parts[0].split()[-40:]  # overlap carried over
