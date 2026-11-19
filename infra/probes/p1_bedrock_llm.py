"""P1 probe: Bedrock LLM IDs, multilingual Converse, forced enum tool-use. Read/invoke only; creates nothing.
Tries eu. Geo profiles first; on denial records the exact error and tests in-Region ON_DEMAND models."""
import argparse
import sys

from p1_common import *  # noqa: F403

PROBE = "p1_bedrock_llm"
PROMPTS = {
    "en": "A customer says: 'My salary has not arrived yet, payroll window was yesterday.' Reply in one short sentence.",
    "ar": "يقول العميل: 'لم يصل راتبي بعد وكان موعد الرواتب أمس.' أجب بجملة قصيرة واحدة بالعربية.",
    "hi": "ग्राहक कहता है: 'मेरी तनख्वाह अभी तक नहीं आई, पेरोल विंडो कल थी।' एक छोटे वाक्य में हिंदी में उत्तर दें।",
    "hinglish": "Customer bolta hai: 'Mera salary abhi tak nahi aaya, payroll window kal thi.' Ek chhote sentence mein Hinglish mein reply do.",
}
ENUM = ["payroll_window", "statutory", "access", "other"]
TOOL = {"toolSpec": {"name": "decide", "description": "Classify the support ticket.", "inputSchema": {"json": {
    "type": "object", "properties": {"choice": {"type": "string", "enum": ENUM}, "confidence": {"type": "number"}},
    "required": ["choice", "confidence"]}}}}
TICKET = "Employee salary file was rejected because the payroll cut-off window had already closed."

# (tier, candidates in preference order, langs)
TIERS = [
    ("cheap", ["eu.anthropic.claude-haiku-5-5", "qwen.qwen3-32b-v1:0", "openai.gpt-oss-20b-1:0", "zai.glm-4.7-flash"], list(PROMPTS)),
    ("hard", ["eu.anthropic.claude-sonnet-5-5", "openai.gpt-oss-120b-1:0", "qwen.qwen3-235b-a22b-2507-v1:0", "deepseek.v3.2"], ["en", "ar"]),
    ("fallback", ["eu.amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0", "eu.amazon.nova-2-lite-v1:0"], ["en", "ar"]),
]


def converse(rt, model, text, tool=False):
    kw = dict(modelId=model, messages=[{"role": "user", "content": [{"text": text}]}], inferenceConfig={"maxTokens": 300, "temperature": 0.0})
    if tool:
        kw["toolConfig"] = {"tools": [TOOL], "toolChoice": {"tool": {"name": "decide"}}}
    resp, ms = timed(rt.converse, **kw)
    u = resp["usage"]
    return resp, ms, u["inputTokens"], u["outputTokens"]


def run(r):
    rt = client("bedrock-runtime")
    br = client("bedrock")
    fms = {m["modelId"]: m for m in br.list_foundation_models()["modelSummaries"]}
    profs = {p["inferenceProfileId"] for p in br.list_inference_profiles()["inferenceProfileSummaries"]}
    for want in ["eu.anthropic.claude-haiku-5-5", "eu.anthropic.claude-sonnet-5-5", "eu.amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0", "eu.amazon.nova-2-lite-v1:0"]:
        check(r, f"id_listed:{want}", want in profs or want in fms, "inference profile" if want in profs else ("foundation model" if want in fms else "absent"))
    r["ids_resolved"]["on_demand_text_models"] = sorted(k for k, m in fms.items() if "ON_DEMAND" in m["inferenceTypesSupported"] and "TEXT" in m["outputModalities"] and "EMBEDDING" not in m["outputModalities"])
    denied = {}
    for tier, cands, langs in TIERS:
        working = None
        for model in cands:
            try:
                resp, ms, ti, to = converse(rt, model, PROMPTS["en"])
            except Exception as e:  # noqa: BLE001
                denied[model] = err_text(e)
                check(r, f"{tier}:{model}:en", False, err_text(e))
                continue
            working = model
            r["est_cost_usd"] += price(model, ti, to)
            r["latency_ms"][f"{tier}:{model}:en"] = ms
            check(r, f"{tier}:{model}:en", True, f"{ms}ms in={ti} out={to}")
            for lang in langs:
                if lang == "en":
                    continue
                try:
                    resp, ms, ti, to = converse(rt, model, PROMPTS[lang])
                    txt = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
                    r["est_cost_usd"] += price(model, ti, to)
                    r["latency_ms"][f"{tier}:{model}:{lang}"] = ms
                    check(r, f"{tier}:{model}:{lang}", len(txt.strip()) > 0, f"{ms}ms in={ti} out={to} text={txt[:60]!r}")
                except Exception as e:  # noqa: BLE001
                    check(r, f"{tier}:{model}:{lang}", False, err_text(e))
            if tier in ("cheap", "fallback"):
                try:
                    resp, ms, ti, to = converse(rt, model, TICKET, tool=True)
                    tu = [b["toolUse"] for b in resp["output"]["message"]["content"] if "toolUse" in b]
                    ok = bool(tu) and tu[0]["input"].get("choice") in ENUM
                    r["est_cost_usd"] += price(model, ti, to)
                    r["latency_ms"][f"{tier}:{model}:tool"] = ms
                    check(r, f"{tier}:{model}:enum_tool", ok, f"{ms}ms in={ti} out={to} input={tu[0]['input'] if tu else None}")
                except Exception as e:  # noqa: BLE001
                    check(r, f"{tier}:{model}:enum_tool", False, err_text(e))
            break  # stop at the first working candidate for this tier
        r["ids_resolved"][tier] = working
    r["ids_resolved"]["denied"] = denied
    if denied:
        r["error"] = "; ".join(f"{k} -> {v}" for k, v in denied.items())
    if not all(r["ids_resolved"].get(t) for t in ("cheap", "hard")):
        r["status"] = "FAIL"
    elif any(not c["pass"] for c in r["checks"] if c["name"].startswith(tuple(f"{t}:{r['ids_resolved'][t]}" for t in r["ids_resolved"] if t in ("cheap", "hard", "fallback") and r["ids_resolved"][t]))):
        r["status"] = "FAIL"
    if denied and r["status"] == "PASS":
        r["fallback"] = "eu./global. Geo profiles denied by FREE-plan SCP; use in-Region ON_DEMAND models listed in ids_resolved"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true")
    if ap.parse_args().cleanup:
        print("nothing to clean (no resources created)")
        return
    r = new_result()
    try:
        run(r)
    except Exception as e:  # noqa: BLE001
        r["status"], r["error"] = "FAIL", err_text(e)
    save(PROBE, r)


if __name__ == "__main__":
    sys.exit(main())
