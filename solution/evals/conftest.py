"""Shared fixtures for the Phase 5 eval gates (pytest solution/evals).

Needs: the compose stack (A7_*_DATABASE_URL, A7_VALKEY_URL), AWS profile paynest for the judge tests, and
reports/p5_harvest.jsonl from `python -m solution.evals.p5_harvest` (the real graph on SAMPLES, seed 20261108).
DEEPEVAL_TELEMETRY_OPT_OUT=1 is forced here. Judges see masked text only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

import pytest  # noqa: E402

REPORTS = Path(__file__).resolve().parent / "reports"
HARVEST = REPORTS / "p5_harvest.jsonl"


@pytest.fixture(scope="session")
def harvest() -> list[dict]:
    if not HARVEST.exists():
        pytest.skip("reports/p5_harvest.jsonl missing: run `uv run python -m solution.evals.p5_harvest`")
    return [json.loads(x) for x in HARVEST.read_text().splitlines() if x.strip()]


@pytest.fixture(scope="session")
def drafts(harvest) -> list[dict]:
    """Harvest rows that have a draft and retrieved contexts (the agent ran)."""
    return [r for r in harvest if (r.get("draft") or "").strip() and r.get("contexts")]


def pytest_configure(config):
    for m in ("eval: Phase 5 eval gate", "judge: calls a Bedrock judge model (costs money, tracked in reports/p5_spend.json)",
              "smoke: small fast subset", "nightly: full-size run"):
        config.addinivalue_line("markers", m)
