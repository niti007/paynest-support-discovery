"""P1 probe: free-tier plan state + Service Quotas (read-only)."""
import argparse
import re

from p1_common import *  # noqa: F403

PROBE = "p1_account"
PATTERNS = {
    "bedrock": [r"claude.*haiku", r"claude.*sonnet", r"nova.*lite", r"qwen3.*32b", r"gpt-oss-120b", r"cohere.*embed", r"titan.*embed"],
    "fargate": [r"fargate on-demand vcpu"],
    "rds": [r"^db instances$"],
    "elasticache": [r"^nodes per region$", r"nodes per cluster"],
    "vpc": [r"interface vpc endpoints per vpc", r"vpc endpoints per region|gateway vpc endpoints"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true")
    if ap.parse_args().cleanup:
        print("nothing to clean (read-only)")
        return
    r = new_result()
    try:
        ft = client("freetier", region="us-east-1").get_account_plan_state()
        ft.pop("ResponseMetadata", None)
        r["ids_resolved"]["account_plan"] = ft
        check(r, "freetier.get_account_plan_state", True, ft)
    except Exception as e:  # noqa: BLE001
        check(r, "freetier.get_account_plan_state", False, err_text(e))
    sq = client("service-quotas")
    quotas = {}
    for svc, pats in PATTERNS.items():
        code = {"fargate": "fargate", "bedrock": "bedrock", "rds": "rds", "elasticache": "elasticache", "vpc": "vpc"}[svc]
        try:
            items = []
            for page in sq.get_paginator("list_service_quotas").paginate(ServiceCode=code):
                items += page["Quotas"]
            hits = [q for q in items if any(re.search(p, q["QuotaName"], re.I) for p in pats)]
            quotas[svc] = [{"name": q["QuotaName"], "code": q["QuotaCode"], "value": q["Value"], "unit": q.get("Unit")} for q in hits]
            check(r, f"quotas:{svc}", bool(hits), f"{len(hits)} of {len(items)} quotas matched")
        except Exception as e:  # noqa: BLE001
            check(r, f"quotas:{svc}", False, err_text(e))
    r["ids_resolved"]["quotas"] = quotas
    if not r["checks"][0]["pass"]:
        r["status"] = "FAIL"
        r["error"] = r["checks"][0]["evidence"]
    save(PROBE, r)


if __name__ == "__main__":
    main()
