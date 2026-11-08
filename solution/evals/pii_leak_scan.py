"""PII leak scan: run pii_gate over every record of every source and rescan the output.

Usage (from repo root): python solution/evals/pii_leak_scan.py
Writes solution/evals/reports/pii_leak_scan.json (ids, field names, types, counts only; no raw values).
Exit code 1 if any leak remains.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # solution/

from a7.nodes.pii_gate import make_pii_gate  # noqa: E402
from a7.pii.patterns import LEGACY_PII  # noqa: E402
from a7.pii.scanner import scan_record  # noqa: E402
from a7.vault import LocalKeyProvider, MemoryVaultStore, Vault  # noqa: E402

DATA = HERE.parents[2] / "data-room" / "data"
REPORT = HERE.parent / "reports" / "pii_leak_scan.json"


def main() -> int:
    tickets = json.loads((DATA / "tickets_export.json").read_text())["tickets"]
    wa = json.loads((DATA / "whatsapp_export.json").read_text())
    calls = json.loads((DATA / "call_center_notes.json").read_text())
    vault = Vault(MemoryVaultStore(), LocalKeyProvider(master_key=bytes(range(32))))  # throwaway key, never persisted
    gate = make_pii_gate(vault)

    # "before" numbers as in analysis.py: unique ids, minus closed-as-duplicate resubmits, legacy PII regexes
    seen, uniq = set(), []
    for t in tickets:
        tid = t.get("ticket_id") or t.get("tkt") or t.get("reference")
        if tid not in seen:
            seen.add(tid)
            uniq.append(t)
    resub = {id(t) for t in uniq if re.search(r"duplicate of PN-", json.dumps(t))}
    real = [t for t in uniq if id(t) not in resub]
    texts = lambda t: " ".join(str(t.get(k, "")) for k in ("subject", "body", "title", "message", "summary", "details"))
    pii_tickets = [t for t in real if any(rx.search(texts(t)) for rx in LEGACY_PII.values())]
    pii_hits = sum(len(rx.findall(texts(t))) for t in real for rx in LEGACY_PII.values())

    report = {"sources": {}, "leaks": [], "placeholders_by_type": {}}
    types: Counter = Counter()
    blocked, leaks = [], []
    for name, recs in (("tickets_export", tickets), ("whatsapp_export", wa), ("call_center_notes", calls)):
        n = 0
        for rec in recs:
            n += 1
            out = gate({"raw_record": rec})
            if out["pii_gate_status"] == "blocked":
                blocked.append((name, out["ticket_id"], out["trace"][-1]["residual"]))
                continue
            for e_type in out["pii_placeholders"].values():
                types[e_type] += 1
            for path, h in scan_record(out["record"]):
                leaks.append({"source": name, "id": out["vault_key"], "field": path, "type": h.type, "by": h.source})
        report["sources"][name] = n
    report.update(
        records_processed=sum(report["sources"].values()),
        tickets_with_pii_before=len(pii_tickets), pii_hits_before=pii_hits, real_tickets=len(real),
        placeholders_by_type=dict(types), blocked=[{"source": s, "id": i, "residual": r} for s, i, r in blocked],
        leaks=leaks, leaks_after_gate=len(leaks) + len(blocked))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"records processed: {report['records_processed']} {report['sources']}")
    print(f"real tickets: {len(real)} | with PII before: {len(pii_tickets)} | hits before: {pii_hits}")
    print(f"placeholders created (vault entries): {dict(types)}")
    print(f"blocked records: {len(blocked)}")
    print(f"LEAKS AFTER GATE: {report['leaks_after_gate']}")
    return 1 if report["leaks_after_gate"] else 0


if __name__ == "__main__":
    sys.exit(main())
