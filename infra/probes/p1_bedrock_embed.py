"""P1 probe: multilingual embeddings (Cohere Embed v4 via eu. profile, Titan V2 in-Region), 1024 dims. Creates nothing."""
import argparse
import json
import math

from p1_common import *  # noqa: F403

PROBE = "p1_bedrock_embed"
TEXTS = {
    "en": "My salary has not arrived yet.",
    "hi": "मेरी तनख्वाह अभी तक नहीं आई है।",
    "ar": "لم يصل راتبي بعد.",
    "control": "The weather in Stockholm is cold in winter.",
}
CANDS = [("cohere", "eu.cohere.embed-v4:0"), ("cohere_global", "global.cohere.embed-v4:0"), ("cohere_base", "cohere.embed-v4:0"), ("titan", "amazon.titan-embed-text-v2:0")]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def embed(rt, kind, model, text):
    if kind.startswith("cohere"):
        body = {"texts": [text], "input_type": "search_document", "embedding_types": ["float"], "output_dimension": 1024}
    else:
        body = {"inputText": text, "dimensions": 1024, "normalize": True}
    resp, ms = timed(rt.invoke_model, modelId=model, body=json.dumps(body), contentType="application/json", accept="*/*")
    out = json.loads(resp["body"].read())
    vec = out["embeddings"]["float"][0] if kind.startswith("cohere") else out["embedding"]
    return vec, ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cleanup", action="store_true")
    if ap.parse_args().cleanup:
        print("nothing to clean (no resources created)")
        return
    r = new_result()
    rt = client("bedrock-runtime")
    working = {}
    errors = {}
    for kind, model in CANDS:
        if kind.startswith("cohere") and any(k.startswith("cohere") for k in working):
            continue  # already have a working Cohere route
        try:
            vecs = {}
            for lang, t in TEXTS.items():
                vecs[lang], ms = embed(rt, kind, model, t)
                r["latency_ms"][f"{model}:{lang}"] = ms
                r["est_cost_usd"] += price(model, len(t) // 3 + 3, 0)
            dims = {k: len(v) for k, v in vecs.items()}
            ok = all(d == 1024 for d in dims.values())
            sims = {"en_hi": cos(vecs["en"], vecs["hi"]), "en_ar": cos(vecs["en"], vecs["ar"]), "en_control": cos(vecs["en"], vecs["control"])}
            check(r, f"{model}:dims1024", ok, f"dims={dims}")
            check(r, f"{model}:semantic", sims["en_hi"] > sims["en_control"] and sims["en_ar"] > sims["en_control"], {k: round(v, 4) for k, v in sims.items()})
            working[kind] = model
            r["ids_resolved"][kind] = model
        except Exception as e:  # noqa: BLE001
            errors[model] = err_text(e)
            check(r, f"{model}:invoke", False, err_text(e))
    r["ids_resolved"]["denied"] = errors
    if errors:
        r["error"] = "; ".join(f"{k} -> {v}" for k, v in errors.items())
    if not working:
        r["status"] = "FAIL"
    elif "titan" not in working and not any(k.startswith("cohere") for k in working):
        r["status"] = "FAIL"
    if any(k.startswith("cohere") for k in working) and "titan" in working:
        r["status"] = "PASS"
    elif working:
        r["status"] = "PASS" if all(c["pass"] for c in r["checks"] if c["name"].split(":")[0] in working.values()) else "FAIL"
        r["fallback"] = "Cohere not reachable; use amazon.titan-embed-text-v2:0" if "titan" in working and not any(k.startswith("cohere") for k in working) else None
    save(PROBE, r)


if __name__ == "__main__":
    main()
