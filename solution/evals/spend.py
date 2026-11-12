"""Bedrock spend ledger for the Phase 5 Builder B runs (hard cap USD 1.00 in total).

Every component that calls Bedrock (graph backends, the DeepEval judges, promptfoo's generator) appends
(component, usd) to a jsonl file. `check()` raises SpendCapExceeded BEFORE a call when the next call could cross the cap.
Prices are per 1M tokens (in, out) from solution/a7/config.py.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

CAP_USD = 1.00
PRICES = {"eu.amazon.nova-lite-v1:0": (0.065, 0.26), "qwen.qwen3-235b-a22b-2507-v1:0": (0.22, 0.88), "qwen.qwen3-32b-v1:0": (0.15, 0.60)}
LEDGER = Path(os.environ.get("A7_SPEND_LEDGER", Path(__file__).parent / "reports" / "phase5b_spend.jsonl"))


class SpendCapExceeded(RuntimeError):
    pass


def usd(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = PRICES.get(model, (0.22, 0.88))
    return (tokens_in * pin + tokens_out * pout) / 1e6


def add(component: str, amount: float, **meta) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"t": round(time.time()), "component": component, "usd": round(amount, 6), **meta}) + "\n")


def total() -> float:
    if not LEDGER.exists():
        return 0.0
    return sum(json.loads(l)["usd"] for l in LEDGER.read_text().splitlines() if l.strip())


def by_component() -> dict[str, float]:
    out: dict[str, float] = {}
    if LEDGER.exists():
        for l in LEDGER.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                out[r["component"]] = out.get(r["component"], 0.0) + r["usd"]
    return {k: round(v, 5) for k, v in out.items()}


def check(next_call_usd: float = 0.01) -> None:
    if total() + next_call_usd > CAP_USD:
        raise SpendCapExceeded(f"Bedrock spend {total():.4f} + {next_call_usd} would exceed the USD {CAP_USD:.2f} cap")
