"""Box 2: normalise. Pure code, no model, no DB.

parse_ts, canon, normalise and is_window are ported verbatim from trainer/analysis.py. Additions:
- parse_called_at: call_center `DD-MM-YYYY HH:MM` (naive, IST).
- parse_time: dispatcher over parse_ts + parse_called_at; tz_label: the original offset of a raw value.
- normalise_any: adds WhatsApp threads and call notes to the three ticket schemas.
- CustomerIndex: exact (case/whitespace-insensitive) customer-name match against customers.csv.
  Never a substring match (Falcon Trading LLC = C-1004, Falcon Contracting LLC = C-1050).

normalise runs AFTER pii_gate on the masked record. Business names (partner end_customer, WhatsApp
customer_hint) are not masked by the gate, and customer ids are structured, so resolution still works.
"""
from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..lang import detect_language
from ..state import TicketState

IST = timezone(timedelta(hours=5, minutes=30))
GST = timezone(timedelta(hours=4))


# ---- verbatim from trainer/analysis.py ---------------------------------------------------------------------
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
# ---- end verbatim ------------------------------------------------------------------------------------------


_CALLED_AT = re.compile(r"^(\d{2})-(\d{2})-(\d{4}) (\d{2}):(\d{2})$")


def parse_called_at(x):
    """call_center_notes `DD-MM-YYYY HH:MM` -> aware IST datetime (naive = IST, same assumption as parse_ts)."""
    if x is None or x == "":
        return None
    m = _CALLED_AT.match(x)
    if not m:
        raise ValueError("not a DD-MM-YYYY HH:MM timestamp")
    d, mo, y, h, mi = map(int, m.groups())
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def parse_time(x):
    """parse_ts for the six ticket formats, plus the call-centre format."""
    if isinstance(x, str) and _CALLED_AT.match(x):
        return parse_called_at(x)
    return parse_ts(x)


def tz_label(x) -> tuple[str, bool]:
    """(original offset, assumed). Epoch ms is absolute UTC. Naive values are assumed IST."""
    if isinstance(x, (int, float)):
        return "+00:00", False
    m = re.match(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2} (IST|GST)$", x)
    if m:
        return ("+05:30" if m.group(1) == "IST" else "+04:00"), False
    if x.endswith("Z"):
        return "+00:00", False
    m = re.search(r"([+-]\d{2}:\d{2})$", x)
    if m:
        return m.group(1), False
    return "+05:30", True


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- customer index ----------------------------------------------------------------------------------------
def _name_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


class CustomerIndex:
    def __init__(self, rows: list[dict[str, str]]):
        self.ids = {r["customer_id"] for r in rows}
        self._by_name: dict[str, list[str]] = {}
        for r in rows:
            self._by_name.setdefault(_name_key(r["name"]), []).append(r["customer_id"])

    @classmethod
    def from_csv(cls, path: str | Path | None = None) -> "CustomerIndex":
        path = Path(path or default_customers_path())
        with path.open(encoding="utf-8", newline="") as f:
            return cls(list(csv.DictReader(f)))

    def by_name(self, name: str | None) -> str | None:
        """Exact name match only. Ambiguous (several ids for one name) -> None."""
        hits = self._by_name.get(_name_key(name or ""), [])
        return hits[0] if len(hits) == 1 else None


def default_customers_path() -> Path:
    root = Path(os.environ.get("A7_DATA_ROOM", Path(__file__).resolve().parents[3] / "data-room"))
    return root / "data" / "customers.csv"


# ---- WhatsApp / call schemas -------------------------------------------------------------------------------
def normalise_any(rec: dict) -> dict:
    """normalise() for tickets, plus WhatsApp threads (`thread_id`) and call notes (`call_id`)."""
    if "thread_id" in rec:
        msgs = rec["messages"]
        evs = [dict(type="message", at=parse_ts(m["ts"]), by=m["from"], d={}) for m in msgs]
        return dict(id=rec["ticket_id"] or rec["thread_id"], channel="whatsapp", cust=None, cust_name=rec.get("customer_hint"),
                    text="\n".join(f'{m["from"]}: {m["text"]}' for m in msgs), cat=None,
                    created=evs[0]["at"] if evs else None, fr=None, resolved=None, events=evs)
    if "call_id" in rec:
        return dict(id=rec["ticket_id"] or rec["call_id"], channel="call", cust=rec.get("customer_id"), text=rec["summary"],
                    cat=None, created=parse_called_at(rec["called_at"]), fr=None, resolved=None, events=[])
    return normalise(rec)


def _raw_time(rec: dict):
    for k in ("received_at", "created", "opened_on", "called_at"):
        if k in rec:
            return rec[k]
    if "messages" in rec and rec["messages"]:
        return rec["messages"][0]["ts"]
    return None


def make_normalise(index: CustomerIndex | None = None) -> Callable[[TicketState], TicketState]:
    idx = index or CustomerIndex.from_csv()

    def normalise_node(state: TicketState) -> TicketState:
        rec = state["record"]
        n = normalise_any(rec)
        raw_t = _raw_time(rec)
        tz, assumed = tz_label(raw_t)
        if n["cust"]:
            ref, how = n["cust"], "given"
        else:
            ref = idx.by_name(n.get("cust_name"))
            how = "name_exact" if ref else "unresolved"
        out: TicketState = dict(state)  # type: ignore[assignment]
        out.update(
            channel=n["channel"],
            received_at=_utc_iso(n["created"]),
            received_tz=tz,
            received_tz_assumed=assumed,
            customer_ref=ref,
            customer_ref_how=how,
            category_raw=n["cat"] or "",
            category=canon(n["cat"]) if n["cat"] else "",
            language=detect_language(state.get("masked_text") or n["text"]),
            is_payroll_window=is_window(n["created"]),
            trace=list(state.get("trace", [])) + [{"node": "normalise", "customer_ref_how": how, "tz_assumed": assumed}],
        )
        return out

    return normalise_node
