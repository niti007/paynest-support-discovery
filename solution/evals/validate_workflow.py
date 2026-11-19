"""Offline sanity check of .github/workflows/eval.yml (actionlint is not installed here): YAML parse + the structural rules we care about.

    uv run python -m solution.evals.validate_workflow
"""
import re
import sys
from pathlib import Path

import yaml

P = Path(__file__).resolve().parents[2] / ".github/workflows/eval.yml"
d = yaml.safe_load(P.read_text())
on = d.get(True, d.get("on"))            # PyYAML reads the bare key `on` as boolean True
errs = []


def need(cond, msg):
    if not cond:
        errs.append(msg)


need(set(on) >= {"pull_request", "schedule", "workflow_dispatch"}, "triggers: pull_request, schedule, workflow_dispatch")
need(re.fullmatch(r"\d+ \d+ \* \* \*", on["schedule"][0]["cron"]), "nightly cron shape")
need(d["permissions"].get("id-token") == "write", "OIDC needs permissions.id-token: write")
need(d["env"].get("DEEPEVAL_TELEMETRY_OPT_OUT") == "1", "DEEPEVAL_TELEMETRY_OPT_OUT=1")
job = d["jobs"]["eval"]
need("pgvector/pgvector" in job["services"]["postgres"]["image"], "pgvector Postgres service container")
need("valkey" in job["services"]["valkey"]["image"], "valkey service container")
steps = job["steps"]
uses = [s.get("uses", "") for s in steps]
uv = [u for u in uses if u.startswith("astral-sh/setup-uv@")]
need(len(uv) == 1 and re.fullmatch(r"astral-sh/setup-uv@[0-9a-f]{40}", uv[0]), "setup-uv pinned by a 40-hex SHA")
need(any(u.startswith("aws-actions/configure-aws-credentials@") for u in uses), "OIDC credentials step")
aws = next(s for s in steps if s.get("uses", "").startswith("aws-actions/configure-aws-credentials"))
need("role-to-assume" in aws["with"] and aws["with"]["aws-region"] == "eu-north-1", "role-to-assume + eu-north-1")
need("AKIA" not in P.read_text(), "no long-lived AWS keys")
runs = [s.get("run", "") for s in steps]
need(any("ci_steps.sh pr" in r for r in runs) and any("ci_steps.sh nightly" in r for r in runs), "pr and nightly commands")
nightly = next(s for s in steps if "ci_steps.sh nightly" in s.get("run", ""))
need(nightly["env"].get("ENABLE_DEEPEVAL_CACHE") == "0", "nightly disables the DeepEval cache")
need("rm -rf .deepeval" in (P.parent.parent.parent / "solution/evals/ci_steps.sh").read_text(), "ci_steps.sh clears the DeepEval cache")
for s in steps:
    need(bool(s.get("uses") or s.get("run")), f"step without uses/run: {s}")
if errs:
    print("INVALID:", *errs, sep="\n  ")
    sys.exit(1)
print(f"eval.yml OK: {len(steps)} steps, triggers {sorted(on)}")
