import re

import pytest

from solution.a7.pii.masker import PlaceholderCounter, mask
from solution.a7.pii.patterns import LEGACY_PII
from solution.a7.pii.scanner import scan, scan_record
from synth import aadhaar, bank, email, eid, iban, pan, phone

IBAN_RE = LEGACY_PII["iban"]
EID_RE = LEGACY_PII["emirates_id"]


def _orig(rec, rx):
    return [m.group(0) for k in ("message", "body", "details") if k in rec for m in rx.finditer(rec[k])]


def test_pn101333_masked_and_revealed(by_id, gate, vault):
    rec = by_id["PN-101333"]
    iban, eid = _orig(rec, IBAN_RE), _orig(rec, EID_RE)
    assert len(iban) == 1 and len(eid) == 1
    out = gate({"raw_record": rec})
    assert out["pii_gate_status"] == "passed"
    assert "raw_record" not in out
    text = out["masked_text"]
    assert "<IBAN_1>" in text and "<EID_1>" in text
    assert not IBAN_RE.search(text) and not EID_RE.search(text)
    assert iban[0] not in str(out) and eid[0] not in str(out)
    assert vault.reveal("PN-101333", "<IBAN_1>", caller="act") == iban[0]
    assert vault.reveal("PN-101333", "<EID_1>", caller="act") == eid[0]
    for who in ("agent", "classify", "", "ACT"):
        with pytest.raises(PermissionError):
            vault.reveal("PN-101333", "<IBAN_1>", caller=who)
    assert out["pii_placeholders"]["<IBAN_1>"] == "IBAN"


def test_same_value_same_placeholder_across_fields():
    c = PlaceholderCounter()
    a, _ = mask(f"Account {iban(7)} please", "T-1", c, nlp=False)
    b, _ = mask(f"again {iban(7, spaced=True)} here", "T-1", c, nlp=False)  # same value, other spacing
    assert "<IBAN_1>" in a and "<IBAN_1>" in b and "<IBAN_2>" not in b


def test_different_values_numbered_in_order():
    c = PlaceholderCounter()
    m, entries = mask(f"{iban(1)} then {iban(2)}", "T-2", c, nlp=False)
    assert m == "<IBAN_1> then <IBAN_2>"
    assert [e.placeholder for e in entries] == ["<IBAN_1>", "<IBAN_2>"]


def test_numbering_shared_across_fields_and_types(gate):
    rec = {"ticket_id": "PN-X1", "channel": "email", "from_email": email(1), "subject": f"mail {email(1)}",
           "body": f"PAN {pan(1)} and {iban(1)}", "received_at": "2026-08-10T09:36:23+05:30",
           "customer_id": "C-1001", "category": "How-To", "events": []}
    out = gate({"raw_record": rec})
    r = out["record"]
    assert r["from_email"] == "<EMAIL_1>" and "<EMAIL_1>" in r["subject"]
    assert "<PAN_1>" in r["body"] and "<IBAN_1>" in r["body"]


@pytest.mark.parametrize("text", [
    "حسابي IBAN الرجاء",          # Arabic
    "मेरा खाता IBAN सही नहीं",       # Hindi
    "mera PAN PANV hai, aadhaar AADH",  # Hinglish
    "هويتيEIDوشكرا",                      # Arabic letters glued to EID
])
def test_patterns_work_in_every_language(text):
    text = text.replace("IBAN", iban(3)).replace("PANV", pan(3)).replace("AADH", aadhaar(3)).replace("EID", eid(3))
    m, entries = mask(text, "T-3", PlaceholderCounter(), nlp=False)
    assert entries and not scan(m, presidio=False)


def test_phone_and_email_and_bank():
    m, e = mask(f"call {phone(1)} or mail {email(2)}; {bank(1)}", "T-4",
                PlaceholderCounter(), nlp=False)
    assert {x.pii_type for x in e} == {"PHONE", "EMAIL", "BANK"}


def test_fail_closed_when_rescan_finds_pii(vault, monkeypatch):
    """If masking misses something, the gate blocks and returns no text."""
    import solution.a7.nodes.pii_gate as g
    monkeypatch.setattr(g, "mask_record", lambda raw, vkey, counter: (raw, []))  # a broken masker
    gate = g.make_pii_gate(vault)
    rec = {"ticket_id": "PN-X2", "channel": "email", "subject": "s", "body": iban(1),
           "from_email": email(3), "received_at": "2026-08-10T09:36:23+05:30", "customer_id": "C-1001",
           "category": "x", "events": []}
    out = gate({"raw_record": rec})
    assert out["pii_gate_status"] == "blocked"
    assert "record" not in out and "masked_text" not in out and "raw_record" not in out
    assert "IBAN" in out["trace"][-1]["residual"]


def test_all_sources_masked_clean(tickets, whatsapp, calls, gate):
    sample = tickets[::7] + whatsapp[::3] + calls[::2]
    for rec in sample:
        out = gate({"raw_record": rec})
        assert out["pii_gate_status"] == "passed"
        assert not list(scan_record(out["record"]))


def test_ticket_pii_set_is_clean(tickets, gate):
    """Every ticket in the export that has legacy PII shows none after the gate."""
    n = 0
    for t in tickets:
        txt = " ".join(str(t.get(k, "")) for k in ("subject", "body", "title", "message", "summary", "details"))
        if any(rx.search(txt) for rx in LEGACY_PII.values()):
            n += 1
            out = gate({"raw_record": t})
            assert out["pii_gate_status"] == "passed"
            assert not any(rx.search(out["masked_text"]) for rx in LEGACY_PII.values())
    assert n >= 101


def test_business_names_survive_the_gate(by_id, gate):
    rec = next(t for t in by_id.values() if t.get("end_customer") == "Falcon Trading LLC")
    out = gate({"raw_record": rec})
    assert out["record"]["end_customer"] == "Falcon Trading LLC"
