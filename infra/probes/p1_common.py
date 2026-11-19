"""Shared helpers for P1 probes (region/profile pinned, result writer). No secrets are logged."""
import json
import pathlib
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

REGION = "eu-north-1"
PROFILE = "paynest"
ACCOUNT = "554559231166"
RESULTS = pathlib.Path(__file__).parent / "results"
TAGS = lambda probe: [{"Key": "project", "Value": "paynest-probe"}, {"Key": "probe", "Value": probe}]  # noqa: E731
TAGS_LC = lambda probe: [{"key": "project", "value": "paynest-probe"}, {"key": "probe", "value": probe}]  # noqa: E731

# USD per 1K tokens (input, output). Approximate public list prices; see solution/a7/config.py.
PRICES = {
    "anthropic.claude-haiku": (0.001, 0.005),
    "anthropic.claude-sonnet": (0.003, 0.015),
    "amazon.nova-lite": (0.00006, 0.00024),
    "amazon.nova-2-lite": (0.0003, 0.0025),
    "qwen.qwen3-32b": (0.00015, 0.0006),
    "openai.gpt-oss-120b": (0.00015, 0.0006),
    "openai.gpt-oss-20b": (0.00007, 0.0003),
    "amazon.titan-embed-text-v2": (0.00002, 0.0),
    "cohere.embed-v4": (0.00012, 0.0),
}


def session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION)


def client(svc, region=REGION, **kw):
    cfg = Config(retries={"max_attempts": 3, "mode": "adaptive"}, region_name=region)
    return boto3.Session(profile_name=PROFILE, region_name=region).client(svc, config=cfg, **kw)


def price(model_id, tin, tout):
    for k, (pi, po) in PRICES.items():
        if k in model_id:
            return tin / 1000 * pi + tout / 1000 * po
    return 0.0


def err_text(e):
    if isinstance(e, ClientError):
        return f"{e.response['Error'].get('Code')}: {e.response['Error'].get('Message')}"
    return f"{type(e).__name__}: {e}"


def new_result():
    return {"status": "PASS", "checks": [], "ids_resolved": {}, "latency_ms": {}, "est_cost_usd": 0.0, "error": None, "fallback": None}


def check(r, name, ok, evidence):
    r["checks"].append({"name": name, "pass": bool(ok), "evidence": evidence})


def save(probe, r):
    RESULTS.mkdir(parents=True, exist_ok=True)
    r["est_cost_usd"] = round(r["est_cost_usd"], 6)
    (RESULTS / f"{probe}.json").write_text(json.dumps(r, indent=2, default=str))
    print(json.dumps({"probe": probe, "status": r["status"], "est_cost_usd": r["est_cost_usd"], "error": r["error"]}, default=str))


def timed(fn, *a, **kw):
    t = time.time()
    out = fn(*a, **kw)
    return out, int((time.time() - t) * 1000)
