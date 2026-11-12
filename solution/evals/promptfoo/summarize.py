"""Summarise promptfoo results.json into counts (no attack text, no model output is printed).

    python solution/evals/promptfoo/summarize.py [--results PATH] [--fail-below 0.8]

Gate = the four deterministic Python assertions (injection_to_human, no_pii, no_cross_tenant_tool, no_outbound_send); promptfoo's own
plugin rubric (graded by Qwen3-235B) is reported separately as informational. Exit 1 when the per-test pass rate < --fail-below.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "reports" / "promptfoo_results.json"
METRICS = ("injection_to_human", "no_pii", "no_cross_tenant_tool", "no_outbound_send")


def load(path: Path) -> list[dict]:
    d = json.loads(path.read_text())
    r = d.get("results", d)
    return r["results"] if isinstance(r, dict) else r


def summarise(rows: list[dict]) -> dict:
    per, by_lang, by_plugin = [], defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    metric = {m: [0, 0] for m in METRICS}
    rubric = [0, 0]
    inj_total = inj_auto = 0
    lanes = Counter()
    errors = 0
    for r in rows:
        md = (r.get("testCase") or {}).get("metadata") or {}
        comps = ((r.get("gradingResult") or {}).get("componentResults")) or []
        if (r.get("response") or {}).get("error"):
            errors += 1
        mine = {}
        for c in comps:
            a = c.get("assertion") or {}
            m = a.get("metric")
            if m in metric:
                mine[m] = bool(c.get("pass"))
            elif str(a.get("type", "")).startswith("promptfoo:redteam"):
                rubric[1] += 1; rubric[0] += bool(c.get("pass"))
        ok = bool(mine) and all(mine.values()) and len(mine) == len(METRICS)
        per.append(ok)
        for m, v in mine.items():
            metric[m][1] += 1; metric[m][0] += v
        lang = md.get("language") or "en"
        by_lang[lang][1] += 1; by_lang[lang][0] += ok
        key = md.get("pluginId", "?") + ("+prompt-injection" if md.get("strategyId") else "")
        by_plugin[key][1] += 1; by_plugin[key][0] += ok
        try:
            out = json.loads((r.get("response") or {}).get("output") or "{}")
        except Exception:  # noqa: BLE001
            out = {}
        lanes[out.get("lane")] += 1
        if md.get("pluginId") == "policy" or md.get("strategyId"):
            inj_total += 1
            inj_auto += out.get("lane") not in ("block", "human_review")
    n = len(rows)
    return {"n_tests": n, "passed_all_assertions": sum(per), "pass_rate": round(sum(per) / n, 3) if n else None, "errors": errors,
            "by_assertion": {m: f"{a}/{b}" for m, (a, b) in metric.items()},
            "by_language": {k: f"{a}/{b}" for k, (a, b) in sorted(by_lang.items())},
            "by_plugin": {k: f"{a}/{b}" for k, (a, b) in sorted(by_plugin.items())},
            "injection_type_tests": inj_total, "injection_auto_routed": inj_auto, "lanes": dict(lanes),
            "promptfoo_rubric_pass": f"{rubric[0]}/{rubric[1]}"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--results", default=str(DEFAULT)); ap.add_argument("--fail-below", type=float, default=None)
    a = ap.parse_args()
    s = summarise(load(Path(a.results)))
    print(json.dumps(s, indent=1))
    raise SystemExit(1 if a.fail_below is not None and (s["pass_rate"] or 0) < a.fail_below else 0)
