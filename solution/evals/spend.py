"""Bedrock spend ledger for the Phase 5 eval suite (Builder A cap: USD 1.00 in total).

Every component that calls Bedrock records its cost here (reports/p5_spend.json, ids and USD only). `guard()` raises
SpendCapExceeded BEFORE a call once the ledger is at the stop line, so nothing runs past the cap.
Prices: solution/a7/config.PRICE_PER_1M_TOKENS (AWS Price List, eu-north-1).
"""
from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path

from solution.a7 import config

LEDGER = Path(__file__).resolve().parent / "reports" / "p5_spend.json"
CAP_USD = 1.00
STOP_USD = 0.95          # stop line: leaves room for one in-flight multi-call metric


class SpendCapExceeded(RuntimeError):
    pass


def price(model: str) -> tuple[float, float]:
    return config.PRICE_PER_1M_TOKENS.get(model) or (0.0, 0.0)


def usd(model: str, tokens_in: int, tokens_out: int) -> float:
    p = price(model)
    return (tokens_in * p[0] + tokens_out * p[1]) / 1e6


def _read() -> dict:
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text())
        except ValueError:
            pass
    return {"cap_usd": CAP_USD, "stop_usd": STOP_USD, "total_usd": 0.0, "by_component": {}, "calls": 0}


def add(component: str, cost: float, calls: int = 1) -> float:
    """Add cost to the ledger (file-locked: tests may run in several processes). Returns the new total."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = _read()
        d["by_component"][component] = round(d["by_component"].get(component, 0.0) + cost, 6)
        d["total_usd"] = round(sum(d["by_component"].values()), 6)
        d["calls"] = d.get("calls", 0) + calls
        d["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        LEDGER.write_text(json.dumps(d, indent=2))
        return d["total_usd"]


def total() -> float:
    return _read()["total_usd"]


def guard() -> None:
    t = total()
    if t >= STOP_USD:
        raise SpendCapExceeded(f"Bedrock spend {t:.4f} USD reached the stop line {STOP_USD} (cap {CAP_USD})")
