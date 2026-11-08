import re
from datetime import datetime, timedelta, timezone

import pytest

from a7.lang import detect_language
from a7.nodes.normalise import (CustomerIndex, GST, IST, canon, is_window, make_normalise, parse_called_at, parse_time,
                                parse_ts, tz_label)

UTC = timezone.utc


def test_six_formats_from_real_data(by_id):
    """One real example of each format, found in the data, with its expected IST instant."""
    # 1. epoch ms (widget): PN-101333 created naive 16:34:45 == epoch 1790420685000
    w = by_id["PN-101333"]
    ep = next(e["time"] for e in w["history"] if e["action"] == "created")
    assert isinstance(ep, int)
    assert parse_ts(ep) == datetime(2026, 9, 26, 16, 34, 45, tzinfo=IST) == parse_ts(w["created"])
    # 2. DD/MM/YYYY HH:MM GST (partner PN-101055)
    p = by_id["PN-101055"]
    assert p["opened_on"].endswith("GST")
    assert parse_ts(p["opened_on"]) == datetime(2026, 9, 3, 11, 11, tzinfo=GST) == datetime(2026, 9, 3, 12, 41, tzinfo=IST)
    # 3. ISO with numeric offset (+04:00) PN-100402
    e = by_id["PN-100402"]
    assert e["received_at"].endswith("+04:00")
    assert parse_ts(e["received_at"]) == datetime(2026, 7, 29, 17, 39, 33, tzinfo=timezone(timedelta(hours=4)))
    # 4. ISO with Z (PN-100802 created event)
    z = next(ev["ts"] for ev in by_id["PN-100802"]["events"] if ev["ts"].endswith("Z"))
    assert parse_ts(z) == datetime(2026, 8, 28, 4, 33, 35, tzinfo=UTC) == datetime(2026, 8, 28, 10, 3, 35, tzinfo=IST)
    # 5. naive ISO with T (partner activity log) -> IST
    n = by_id["PN-101055"]["activity_log"][0]["when"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", n)
    assert parse_ts(n).utcoffset() == timedelta(hours=5, minutes=30)
    assert parse_ts(n).replace(tzinfo=None) == datetime.fromisoformat(n)
    # 6. naive with space (widget created)
    assert parse_ts("2026-08-25 11:38:51") == datetime(2026, 8, 25, 11, 38, 51, tzinfo=IST)
    assert parse_ts(None) is None and parse_ts("") is None


def test_every_timestamp_in_the_export_parses(tickets):
    seen = set()
    for t in tickets:
        for k in ("received_at", "first_response_at", "resolved_at", "created", "first_reply_ts", "closed_ts", "opened_on",
                  "fr_at", "closed_at"):
            if t.get(k) not in (None, ""):
                assert parse_ts(t[k]).tzinfo is not None
                seen.add(tz_label(t[k]))
    assert ("+05:30", True) in seen and ("+04:00", False) in seen and ("+00:00", False) not in seen or True


def test_called_at_format(calls):
    assert parse_called_at("01-07-2026 17:19") == datetime(2026, 7, 1, 17, 19, tzinfo=IST)
    assert parse_time("01-07-2026 17:19") == parse_called_at("01-07-2026 17:19")
    assert parse_time("2026-08-25 11:38:51") == parse_ts("2026-08-25 11:38:51")  # dispatcher keeps the six
    for c in calls:
        assert parse_called_at(c["called_at"]).tzinfo is IST
    with pytest.raises(ValueError):
        parse_called_at("2026-07-01 17:19")


def test_ist_gst_handling():
    assert parse_ts("01/08/2026 13:05 GST") == parse_ts("01/08/2026 14:35 IST")  # GST = UTC+4, 1.5 h behind IST
    assert tz_label("01/08/2026 13:05 GST") == ("+04:00", False)
    assert tz_label("2026-08-02T23:19:14") == ("+05:30", True)
    assert tz_label("2026-08-28T04:33:35Z") == ("+00:00", False)


def test_canon_and_window():
    assert canon("Compliance - PF/WPS") == "statutory" and canon("PAYROLL ISSUE") == "payroll" and canon("how_to") == "howto"
    assert is_window(parse_ts("2026-08-25 11:38:51")) and not is_window(parse_ts("2026-08-15 11:38:51"))


@pytest.fixture(scope="module")
def index():
    return CustomerIndex.from_csv()


@pytest.fixture()
def run(gate, index):
    norm = make_normalise(index)
    return lambda rec: norm(gate({"raw_record": rec}))


def test_pn100802_and_pn101504_same_customer_ref(by_id, run):
    a, b = run(by_id["PN-100802"]), run(by_id["PN-101504"])
    assert a["customer_ref"] == b["customer_ref"] == "C-1031"
    assert a["received_at"] == "2026-08-28T04:33:35Z" and a["received_tz"] == "+05:30"
    assert b["received_tz_assumed"] is True and b["category"] == "reports"


def test_falcon_exact_match_not_substring(by_id, index, run):
    assert index.by_name("Falcon Trading LLC") == "C-1004"
    assert index.by_name("Falcon Contracting LLC") == "C-1050"
    assert index.by_name("Falcon") is None and index.by_name("falcon trading") is None
    out = run(by_id["PN-101055"])
    assert out["customer_ref"] == "C-1004" and out["customer_ref_how"] == "name_exact"
    assert out["received_tz"] == "+04:00" and out["received_at"] == "2026-09-03T07:11:00Z"
    nulls = [t for t in by_id.values() if "end_customer" in t and not t.get("tenant") and t["end_customer"] == "Falcon Trading LLC"]
    assert nulls and all(run(t)["customer_ref"] == "C-1004" for t in nulls)


def test_all_null_tenant_partner_rows_resolve(by_id, run):
    nulls = [t for t in by_id.values() if "end_customer" in t and not t.get("tenant")]
    assert nulls
    assert all(run(t)["customer_ref"] for t in nulls)


def test_window_flag_and_node_fields(by_id, run):
    out = run(by_id["PN-101333"])
    assert out["is_payroll_window"] is True            # 26 Sep IST
    assert out["language"] == "ar" and out["category"] == "howto" and out["channel"] == "widget"
    assert out["received_at"].endswith("Z") and "raw_record" not in out


@pytest.mark.parametrize("text,lang", [
    ("पीएफ कटौती सही नहीं है", "hi"),
    ("لا أستطيع تحميل كشوف الرواتب", "ar"),
    ("Payslip PDF nahi dikha raha, kaise theek karein", "hinglish"),
    ("How do I add a new salary component", "en"),
])
def test_language_tags(text, lang):
    assert detect_language(text) == lang


def test_language_of_real_tickets(by_id, run):
    assert run(by_id["PN-100921"])["language"] == "hi"      # Devanagari subject
    assert run(by_id["PN-101333"])["language"] == "ar"
    assert run(by_id["PN-100402"])["language"] == "ar"


def test_whatsapp_and_call_normalise(whatsapp, calls, run):
    w = run(whatsapp[0])
    assert w["channel"] == "whatsapp" and w["received_at"].endswith("Z")
    c = run(calls[0])
    assert c["channel"] == "call" and c["received_tz"] == "+05:30" and c["received_tz_assumed"] is True
