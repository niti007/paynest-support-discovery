"""P1 probe (read-only): is AgentCore Runtime / SageMaker reachable for hosting the Strands Decider 2B model?"""
import argparse
import re

from p1_common import *  # noqa: F403

PROBE = "p1_decider_hosting"


def attempt(r, name, fn):
    try:
        out = fn()
        check(r, name, True, out)
        return True
    except Exception as e:  # noqa: BLE001
        check(r, name, False, err_text(e))
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true")
    if ap.parse_args().cleanup:
        print("nothing to clean (read-only)")
        return
    r = new_result()
    ac, sm, sq = client("bedrock-agentcore-control"), client("sagemaker"), client("service-quotas")
    a = attempt(r, "agentcore.list_agent_runtimes", lambda: f"reachable, {len(ac.list_agent_runtimes()['agentRuntimes'])} runtimes")
    s = attempt(r, "sagemaker.list_endpoints", lambda: f"reachable, {len(sm.list_endpoints()['Endpoints'])} endpoints")

    def q():
        items = []
        for p in sq.get_paginator("list_service_quotas").paginate(ServiceCode="sagemaker"):
            items += p["Quotas"]
        hits = {x["QuotaName"]: x["Value"] for x in items if re.search(r"serverless|ml\.m5\.large for endpoint usage|ml\.g5\.xlarge for endpoint usage", x["QuotaName"], re.I)}
        r["ids_resolved"]["sagemaker_quotas"] = hits
        return f"{len(hits)} matched"
    attempt(r, "sagemaker.service_quotas", q)
    r["ids_resolved"].update(default="ECS Fargate 2 vCPU / 8 GB", agentcore_runtime_reachable=a, sagemaker_reachable=s)
    errs = [c["evidence"] for c in r["checks"] if not c["pass"]]
    r["error"] = "; ".join(errs) or None
    r["status"] = "PASS" if (a or s) else "BLOCKED"
    if r["status"] == "BLOCKED":
        r["fallback"] = "ECS Fargate 2 vCPU / 8 GB (default)"
    save(PROBE, r)


if __name__ == "__main__":
    main()
