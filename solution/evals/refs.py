"""Reference answers and context formatting shared by the RAGAS and DeepEval gates (derived from statutory_rules + received date)."""
import json
from pathlib import Path

SETS = Path(__file__).resolve().parent / "sets"

REFERENCE = {   # (rule, in_force) -> reference answer, from the statutory_rules table
    ("R-UAE-WPS-2026-07", True): "From 15 July 2026 the WPS SIF file uses the v3 layout with 16 columns and the new EDR layout (rule R-UAE-WPS-2026-07). "
                                 "The older v2 layout with 14 columns (KB-0201, KB-0202) no longer applies.",
    ("R-UAE-WPS-2026-07", False): "Before 15 July 2026 the WPS SIF file uses the v2 layout with 14 columns (KB-0201, KB-0202); v3 with 16 columns applies from 15 July 2026.",
    ("R-IN-PF-2026-08", True): "From 1 August 2026 the EPF statutory wage ceiling is INR 25,000 per month (rule R-IN-PF-2026-08). "
                               "The earlier INR 15,000 ceiling (KB-0142, KB-0143) no longer applies.",
    ("R-IN-PF-2026-08", False): "Before 1 August 2026 the EPF statutory wage ceiling is INR 15,000 per month (KB-0142, KB-0143); INR 25,000 applies only from 1 August 2026.",
}
EFFECTIVE = {"R-UAE-WPS-2026-07": "2026-07-15", "R-IN-PF-2026-08": "2026-08-01"}


def meta_by_id() -> dict[str, dict]:
    out = {}
    for n in ("stale", "control"):
        for x in (json.loads(l) for l in (SETS / f"{n}.jsonl").read_text().splitlines() if l.strip()):
            out[x["id"]] = {**x["meta"], "set": n}
    return out


def reference_for(row: dict, meta: dict) -> str:
    return REFERENCE[(meta["rule"], row["as_of"] >= EFFECTIVE[meta["rule"]])]


def ctx_text(c: dict) -> str:
    eff = f" (effective from {c['effective_from']})" if c.get("effective_from") else ""
    return f"[{c['source_id']}]{eff} {c['body']}"


