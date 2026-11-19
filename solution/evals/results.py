"""Measured Gate 5 results, one entry per criterion, merged into reports/p5_results.json by the pytest gates.

`record()` stores the measured value, n, the Session 4 s4.1 threshold and PASS/FAIL (computed by the caller from the real
measurement; nothing is redefined here). `p5_card.py` renders reports/results_card_phase5.md from this file.
"""
from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path

PATH = Path(__file__).resolve().parent / "reports" / "p5_results.json"


def record(key: str, criterion: str, threshold: str, value: str, n: str, passed: bool | None, notes: str = "", exception: bool = False) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PATH.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = json.loads(PATH.read_text()) if PATH.exists() else {}
        d[key] = {"criterion": criterion, "threshold": threshold, "value": value, "n": n,
                  "status": "PASS" if passed else (("EXCEPTION" if exception else "FAIL") if passed is False else "INFO"), "notes": notes,
                  "measured_at": time.strftime("%Y-%m-%d %H:%M")}
        PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False))


def load() -> dict:
    return json.loads(PATH.read_text()) if PATH.exists() else {}
