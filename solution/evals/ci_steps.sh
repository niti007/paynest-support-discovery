#!/bin/bash
# The eval commands of .github/workflows/eval.yml, in one place so CI and a local run execute exactly the same thing.
#   bash solution/evals/ci_steps.sh pr        smoke + stale + safety + routing (samples) + offline unit/memory tests
#   bash solution/evals/ci_steps.sh nightly   the same, then the full sets, the promptfoo red team and the judge cross-check
# A7_BEDROCK=0 (set by the workflow when AWS_EVAL_ROLE_ARN is absent): only the offline steps run, the Bedrock steps are skipped with a notice.
# Needs: Postgres (pgvector) + Valkey reachable through A7_* env, AWS credentials for Bedrock eu-north-1, `uv sync` done.
set -euo pipefail
cd "$(dirname "$0")/../.."
export DEEPEVAL_TELEMETRY_OPT_OUT=1
MODE="${1:-pr}"
BEDROCK="${A7_BEDROCK:-1}"
FAILED=0
step() { echo; echo "=== $* ==="; }
run() { "$@" || FAILED=1; }       # keep going so one report shows every red step; the script still exits 1 at the end

step "prepare data (schema, reference data, KB + embeddings, bootstrap)"
uv run python -m solution.a7.db apply
uv run python -m solution.ingest.load_reference
if [ "$BEDROCK" = "1" ]; then uv run python -m solution.ingest.load_kb --embed; else uv run python -m solution.ingest.load_kb; fi
uv run python -m solution.db.bootstrap

step "offline unit + memory tests (no Bedrock)"
run uv run pytest solution/tests -m "not aws and not model" -q -x

if [ "$BEDROCK" != "1" ]; then
  echo "::notice title=Bedrock eval skipped::A7_BEDROCK=0 (no AWS role), so smoke, stale, safety, routing and nightly steps were skipped; offline steps only."
  if [ "$FAILED" -ne 0 ]; then echo; echo "EVAL CI: RED (offline steps)"; exit 1; fi
  echo; echo "EVAL CI: GREEN (offline only, Bedrock steps skipped)"; exit 0
fi

step "smoke: the four demo tickets through the real graph"
run uv run pytest solution/tests/test_p3_gate.py -m aws -q

step "stale + safety + routing ($MODE)"
run uv run python -m solution.evals.ci_gate "$MODE"

if [ "$MODE" = "nightly" ]; then
  step "nightly: clear the DeepEval cache so judge results are not replayed"
  rm -rf .deepeval "${DEEPEVAL_CACHE_FOLDER:-.deepeval_cache}"
  export ENABLE_DEEPEVAL_CACHE=0
  step "nightly: promptfoo red team (committed generated attacks, real graph)"
  run bash -c 'cd solution/evals/promptfoo && ./pf.sh run -c redteam.generated.yaml -o ../reports/promptfoo_results.json --no-cache --max-concurrency 1; rc=$?; [ $rc -eq 0 ] || [ $rc -eq 100 ]'   # exit 100 = some tests failed; summarize.py below is the gate
  run uv run python solution/evals/promptfoo/summarize.py --fail-below "$(uv run python -c 'from solution.evals import floors; print(floors.floor("redteam_pass"))')"
  step "nightly: judge cross-check (Qwen3-235B vs Nova Lite, n=24)"
  run uv run python -m solution.evals.judge_crosscheck --n 24
fi

if [ "$FAILED" -ne 0 ]; then echo; echo "EVAL CI: RED"; exit 1; fi
echo; echo "EVAL CI: GREEN"
