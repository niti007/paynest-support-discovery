"""P1 probe: head-to-head model candidates (EN/AR/HI/Hinglish + forced enum tool), real prices from the
AWS Price List API (read-only, us-east-1 endpoint), quotas from Service Quotas. Creates nothing.
Writes infra/probes/results/p1_model_candidates.json. The user confirms the final model choice."""
import argparse
import json
import re
import statistics

from p1_common import *  # noqa: F403
from p1_bedrock_llm import PROMPTS, TICKET, ENUM, converse

PROBE = "p1_model_candidates"
# (tier candidate, model id, pricing service, pricing model name, quota key regex)
CANDS = [
    ("cheap", "eu.anthropic.claude-haiku-4-5-20251001-v1:0", "AmazonBedrockFoundationModels", "Claude Haiku 4.5", r"Anthropic Claude Haiku 4\.5$"),
    ("cheap", "eu.amazon.nova-lite-v1:0", "AmazonBedrock", "Nova Lite", r"Amazon Nova Lite$"),
    ("cheap", "qwen.qwen3-32b-v1:0", "AmazonBedrock", "Qwen3 32B", r"Qwen3 32B"),
    ("hard", "eu.anthropic.claude-sonnet-4-6", "AmazonBedrockFoundationModels", "Claude Sonnet 4.6", r"Anthropic Claude Sonnet 4\.6$"),
    ("hard", "qwen.qwen3-235b-a22b-2507-v1:0", "AmazonBedrock", "Qwen3 235B A22B 2507", r"Qwen3 235B"),
    ("hard", "openai.gpt-oss-120b-1:0", "AmazonBedrock", "gpt-oss-120b", r"gpt-oss-120b|GPT OSS 120B"),
]
SCRIPT = {"en": r"[A-Za-z]", "hinglish": r"[A-Za-z]", "ar": r"[؀-ۿ]", "hi": r"[ऀ-ॿ]"}


def per_1m(price, unit):
    return float(price) * (1000 if unit.lower().startswith("1k") else 1)


def get_price(pc, service, model, geo):
    """Return (in_per_1M, out_per_1M, note) for eu-north-1. geo=True -> regional/geo (non-Global) rows of cross-Region models."""
    found = {}
    for page in pc.get_paginator("get_products").paginate(ServiceCode=service, Filters=[
            {"Type": "TERM_MATCH", "Field": "model", "Value": model}, {"Type": "TERM_MATCH", "Field": "regionCode", "Value": REGION}]):
        for x in page["PriceList"]:
            r = json.loads(x)
            a, u = r["product"]["attributes"], r["product"]["attributes"].get("usagetype", "")
            if any(k in u.lower() for k in ("batch", "cache", "priority", "flex", "mantle", "reserved", "custom")):
                continue
            if service == "AmazonBedrockFoundationModels" and (("Global" in u) == geo):
                continue
            kind = "in" if "input" in a.get("inferenceType", "").lower() else "out" if "output" in a.get("inferenceType", "").lower() else None
            if not kind:
                continue
            d = list(list(r["terms"]["OnDemand"].values())[0]["priceDimensions"].values())[0]
            found[kind] = (per_1m(d["pricePerUnit"]["USD"], d["unit"]), u)
    if "in" in found and "out" in found:
        return found["in"][0], found["out"][0], f"{found['in'][1]} / {found['out'][1]}"
    return None, None, "no Price List row"  # TODO: price unknown, do not guess


def get_quota(items, regex, cross):
    pre = "Cross-region" if cross else "On-demand"
    out = {}
    for q in items:
        n = q["QuotaName"]
        for kind, lab in (("requests", "rpm"), ("tokens", "tpm")):
            if n.startswith(f"{pre} model inference {kind} per minute for") and "Global" not in n and "1M Context" not in n and re.search(regex, n):
                out[lab] = int(q["Value"])
    return out or None


def lang_ok(lang, text):
    return bool(re.search(SCRIPT[lang], text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true")
    if ap.parse_args().cleanup:
        print("nothing to clean (no resources created)")
        return
    r = new_result()
    rt, br, pc, sq = client("bedrock-runtime"), client("bedrock"), client("pricing", region="us-east-1"), client("service-quotas")
    profs = {p["inferenceProfileId"] for p in br.list_inference_profiles()["inferenceProfileSummaries"]}
    fms = {m["modelId"]: m["inferenceTypesSupported"] for m in br.list_foundation_models()["modelSummaries"]}
    items = [q for pg in sq.get_paginator("list_service_quotas").paginate(ServiceCode="bedrock") for q in pg["Quotas"]]
    table = []
    for tier, model, svc, pname, qre in CANDS:
        geo = model.startswith("eu.")
        row = {"model": model, "tier_candidate": tier, "listed": model in profs or model in fms, "langs": {}, "enum_valid": None, "errors": {}}
        lat = []
        for lang in PROMPTS:
            try:
                resp, ms, ti, to = converse(rt, model, PROMPTS[lang])
                txt = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
                row["langs"][lang] = lang_ok(lang, txt)
                lat.append(ms)
                r["est_cost_usd"] += 0.0005
            except Exception as e:  # noqa: BLE001
                row["langs"][lang] = False
                row["errors"][lang] = err_text(e)
        try:
            resp, ms, ti, to = converse(rt, model, TICKET, tool=True)
            tu = [b["toolUse"] for b in resp["output"]["message"]["content"] if "toolUse" in b]
            row["enum_valid"] = bool(tu) and tu[0]["input"].get("choice") in ENUM
            row["enum_output"] = tu[0]["input"] if tu else None
            lat.append(ms)
            r["est_cost_usd"] += 0.0005
        except Exception as e:  # noqa: BLE001
            row["enum_valid"] = False
            row["errors"]["enum_tool"] = err_text(e)
        row["p50_latency_ms"] = int(statistics.median(lat)) if lat else None
        try:
            pin, pout, note = get_price(pc, svc, pname, geo)
        except Exception as e:  # noqa: BLE001
            pin, pout, note = None, None, err_text(e)
        row.update(usd_per_1m_in=pin, usd_per_1m_out=pout, price_source=f"Price List API {svc} / {pname} / {note}")
        row["quota"] = get_quota(items, qre, geo)
        row["pass"] = all(row["langs"].values()) and bool(row["enum_valid"])
        table.append(row)
        check(r, f"{tier}:{model}", row["pass"], {k: row[k] for k in ("langs", "enum_valid", "p50_latency_ms")} | ({"errors": row["errors"]} if row["errors"] else {}))
    r["ids_resolved"]["table"] = table
    errs = {x["model"]: x["errors"] for x in table if x["errors"]}
    r["error"] = json.dumps(errs, ensure_ascii=False) if errs else None
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "p1_model_candidates.json").write_text(json.dumps({"date": "2026-11-08", "region": REGION, "note": "user confirms the final model choice", "table": table}, indent=2, ensure_ascii=False))
    save(PROBE, r)
    for x in table:
        print(x["tier_candidate"], x["model"], "PASS" if x["pass"] else "FAIL", x["langs"], x["enum_valid"], x["p50_latency_ms"], x["usd_per_1m_in"], x["usd_per_1m_out"], x["quota"])


if __name__ == "__main__":
    main()
