"""Build + embed the dedupe corpus (all 1,353 unique export tickets, MASKED text) and cache it to a file.

Cache: solution/evals/cache/dedupe_corpus.json (meta + masked text, no vectors) and dedupe_vecs.npy (float32, n x 1024).
Re-running only embeds ids missing from the cache. Bedrock Cohere v4 via solution.a7.embed (profile paynest, eu-north-1).

Usage: uv run python solution/evals/dedupe_corpus.py [--embed]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))
sys.path.insert(0, str(HERE.parent))

CACHE = HERE.parent / "cache"
CORPUS = CACHE / "dedupe_corpus.json"
VECS = CACHE / "dedupe_vecs.npy"
SETS = HERE.parent / "sets"
PRICE_PER_1M = 0.12


def build_corpus() -> list[dict]:
    import build_sets as bs
    real, uniq, _ = bs.load_real()
    pipe = bs.Pipeline()
    routing = {json.loads(l)["id"]: json.loads(l) for l in (SETS / "routing.jsonl").open()}
    incident = {}
    for l in (SETS / "incident.jsonl").open():
        r = json.loads(l)
        incident[r["id"]] = r["meta"]["incident"]
    out = []
    for t in uniq:
        tid = bs.raw_id(t)
        s = pipe.run(t)
        m = re.search(r"duplicate of (PN-\d+)", json.dumps(t))
        out.append({"id": tid, "tenant": s["customer_ref"], "received_at": s["received_at"], "channel": s["channel"],
                    "text": s["masked_text"], "routing": tid in routing, "split": routing.get(tid, {}).get("split"),
                    "incident": incident.get(tid), "parent": m.group(1) if m else None})
    out.sort(key=lambda r: (r["received_at"], r["id"]))
    return out


def load() -> tuple[list[dict], np.ndarray]:
    return json.loads(CORPUS.read_text()), np.load(VECS)


def main() -> int:
    CACHE.mkdir(exist_ok=True)
    corpus = build_corpus()
    old = {}
    if CORPUS.exists() and VECS.exists():
        oc, ov = load()
        old = {r["id"]: (r["text"], ov[i]) for i, r in enumerate(oc)}
    need = [i for i, r in enumerate(corpus) if r["id"] not in old or old[r["id"]][0] != r["text"]]
    needset = set(need)
    vecs = np.zeros((len(corpus), 1024), dtype=np.float32)
    for i, r in enumerate(corpus):
        if i not in needset:
            vecs[i] = old[r["id"]][1]
    if need and "--embed" in sys.argv:
        from solution.a7.embed import embed
        got = embed([corpus[i]["text"] for i in need], "search_document")
        for i, v in zip(need, got):
            vecs[i] = v
        chars = sum(len(corpus[i]["text"]) for i in need)
        print(f"embedded {len(need)} texts, ~{chars/4:.0f} tokens, ~USD {chars/4/1e6*PRICE_PER_1M:.4f}")
    elif need:
        print(f"{len(need)} texts missing from cache; pass --embed")
        return 1
    CORPUS.write_text(json.dumps(corpus, ensure_ascii=False))
    np.save(VECS, vecs)
    print("corpus", len(corpus), "tenantless", sum(r["tenant"] is None for r in corpus))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
