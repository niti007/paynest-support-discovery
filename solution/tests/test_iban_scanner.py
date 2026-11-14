"""Generic IBAN detection in the leak scanner (any country, mod-97) and ids that must not look like one."""
from solution.a7.pii.scanner import iban_spans, mask_ids, scan

IBANS = {"DE": "DE89 3704 0044 0532 0130 00", "DE_compact": "DE89370400440532013000", "GB": "GB82 WEST 1234 5698 7654 32",
         "GB_compact": "GB82WEST12345698765432", "AE": "AE070331234567890123456", "AE_grouped": "AE07 0331 2345 6789 0123 456",
         "AD": "AD1200012030200359100100", "FR": "FR1420041010050500013M02606"}


def test_iban_any_country_is_detected():
    for name, v in IBANS.items():
        text = f"please pay salary to IBAN {v} today"
        assert any(h.type == "IBAN" for h in scan(text, presidio=False, ner=False)), name
        if not name.startswith("AE"):
            spans = iban_spans(text)
            assert spans and text[spans[0][0]:spans[0][1]] == v, name


def test_wrong_checksum_is_not_a_generic_iban():
    assert iban_spans("ref DE00 3704 0044 0532 0130 00 x") == []


def test_hex_ids_and_uuids_are_not_ibans():
    ids = ["1f0d3c2a-7b9e-4c11-9a62-123456789012", "1F0D3C2A-7B9E-4C11-9A62-123456789012", "0123456789abcdef0123456789abcdef",
           "AB12CDEF34AB56CD78EF90AB12CD34EF", "5a3b9c0d1e2f4a5b6c7d8e9f0a1b2c3d", "PN-101055 CL-202413 R-IN-PF-2026-08"]
    for i in ids:
        assert iban_spans(f"id {i} end") == [], i
        assert [h for h in scan(f"checkpoint {i} end", presidio=False, ner=False) if h.type == "IBAN"] == [], i


def test_id_mask_never_swallows_an_iban():
    ad = f"acct {IBANS['AD']} end"        # AD + 22 digits is 24 hex chars: looks like a long hex id
    assert mask_ids(ad) == ad
    assert mask_ids("thread 1f0d3c2a-7b9e-4c11-9a62-123456789012").count("_") == 36
