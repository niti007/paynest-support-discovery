"""Render reports/results_card_phase5.md from reports/p5_results.json (written by the pytest gates) and the spend ledger.

    uv run python -m solution.evals.p5_card

The card shows each Gate 5 criterion with its MEASURED value, n, PASS/FAIL and notes. FAIL rows are bold. Nothing is redefined here:
thresholds are the Session 4 s4.1 values quoted in the plan. Prose blocks (probe, Langfuse, open issues) are in PROSE below.
"""
from __future__ import annotations

import json
from pathlib import Path

from solution.evals import results, spend

OUT = Path(__file__).resolve().parent / "reports" / "results_card_phase5.md"
ORDER = [("pii", "0 PII leaks"), ("routing", "Routing >= 95%"), ("routing_regression", "Routing no-regression (proposed replacement)"),
         ("injection", "Injection 100% / < 2% false positives"), ("stale_chunks", "0 stale chunks"), ("faithfulness", "Faithfulness >= 0.9"),
         ("guard_wps", "Guard catches '14 columns'"), ("guard_pf", "Guard catches 'INR 15,000'"), ("faithfulness_stale", "Judge scores stale drafts low"),
         ("cost", "<= USD 0.02 per draft"), ("latency", "p95 < 3 s")]
EXTRA = ["dedupe_meridian", "dedupe_false_incidents", "dedupe_resubmits", "rule_retrieved", "ragas_stale", "ragas_control", "correctness_stale",
         "correctness_control", "dated_citation_stale", "dated_citation_control", "langfuse_export_masking", "langfuse_masking", "langfuse_dataset", "p1"]


def row(r: dict) -> str:
    st = f"**{r['status']}**" if r["status"] == "FAIL" else r["status"]
    return f"| {r['criterion']} | {r['threshold']} | {r['value']} | {r['n']} | {st} | {r['notes'].replace('|', '/')} |"


def main() -> None:
    res = results.load()
    sp = json.loads(spend.LEDGER.read_text())
    head = "| criterion | Session 4 s4.1 threshold | measured | n | result | notes |\n|---|---|---|---|---|---|"
    L = ["# Phase 5 results card (Builder A: eval suite)", "",
         "All numbers are MEASURED on SAMPLES, not full sets, with fixed seed 20261108 and the exact n shown. Real graph runs (Nova Lite routes, "
         "Qwen3-235B drafts, compose Postgres/Valkey, mock payroll API), eu-north-1, profile paynest, masked text only. Judges: DeepEval and RAGAS "
         "use Qwen3-235B. Known conflicts are reported as FAIL, not redefined.", "",
         f"Bedrock spend for the whole suite: USD {sp['total_usd']:.4f} of the USD {sp['cap_usd']:.2f} cap ("
         + ", ".join(f"{k} {v:.4f}" for k, v in sorted(sp['by_component'].items())) + ").", "", "## Gate 5 criteria", "", head]
    L += [row(res[k]) for k, _ in ORDER if k in res]
    L += ["", "## Supporting measurements", "", head]
    L += [row(res[k]) for k in EXTRA if k in res]
    L += [""] + PROSE
    OUT.write_text("\n".join(L))
    fails = [res[k]["criterion"] for k, _ in ORDER if k in res and res[k]["status"] == "FAIL"]
    print(f"wrote {OUT}; FAIL: {fails}")


PROSE = [
    "## Judge probe", "",
    "deepeval 4.3.3 `AmazonBedrockModel` (native, Converse API via aiobotocore) works with `qwen.qwen3-235b-a22b-2507-v1:0` and `eu.amazon.nova-lite-v1:0` in eu-north-1: "
    "plain generate returned text and `FaithfulnessMetric` ran its JSON-schema steps end to end on both. No custom `DeepEvalBaseLLM` wrapper was needed. "
    "`solution/evals/judges.py` subclasses it only to book each call's cost in the spend ledger and to stop at the stop line (USD 0.95).", "",
    "## Langfuse status", "",
    "SDK `langfuse==5.2.0` with the matching self-hosted server `langfuse/langfuse` and `langfuse/langfuse-worker` **4.79.0** (tag verified on Docker Hub, 2026-11-12), "
    "behind `profiles: [langfuse]` in `docker-compose.yml`; the old untested `:3` images are replaced, MinIO now `cgr.dev/chainguard/minio` (Docker Hub `minio/minio` is gone) and "
    "ClickHouse `26.3.62.3` as in the upstream compose. The full stack (Postgres, Redis, ClickHouse, MinIO, web, worker) ran on this Mac with 4 GB Docker memory. "
    "The server is in v4 `events_only` mode: the legacy `/api/public/traces` read is disabled, so tests read back through `lf.api.observations.get_many` (v2). "
    "Masking uses `mask_otel_spans` (`solution/a7/tracing.py`); `worker.run_ticket` adds the LangGraph callback only when `LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY` are set.", "",
    "## Notes on method", "",
    "- Faithfulness is judged against the retrieved chunks only. Values the agent takes from payroll tools or the ticket are not in that context.",
    "- RAGAS 0.4.3 does not import with langchain-core >= 1 (it imports a removed Vertex module). `solution/evals/ragas_compat.py` registers inert stubs first; one RAGAS import path (classic `ragas.metrics`) is used per module.",
    "- References for RAGAS and correctness come from the `statutory_rules` table and the ticket's received date, not from model output.",
    "- Dedupe numbers are re-measured offline on the cached Cohere embeddings (Phase 4 chosen values); the Phase 4 grid was extended after the eval split was seen, so they are not a blind test.",
]

if __name__ == "__main__":
    main()
