"""PII leak scan over every place a run can leave text: harvest drafts, outbox, approvals, audit log, answer_cache, escalations,
shadow reviews, LangGraph checkpoint tables, and Valkey. Presidio + the regex/legacy layers (solution.a7.pii.scanner.scan), plus a
"canary" check: the real PII VALUES found in the raw source records of the harvested tickets must not occur literally in any store.

Reports store name, hit type and counts only. Raw values live in memory only and are never printed or written.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Iterator

from solution.a7 import db
from solution.a7.pii.scanner import ID_SPAN_RE, scan   # scan() masks UUID / long-hex ids itself (mask_ids)

TEXT_TABLES = ["outbox", "approvals", "audit_log", "answer_cache", "escalations", "shadow_reviews", "incidents", "kb_suggestions",
               "partner_map_suggestions"]
VEC_RE = re.compile(r"\[(?:-?\d+(?:\.\d+)?(?:e-?\d+)?,){50,}-?\d+(?:\.\d+)?(?:e-?\d+)?\]")      # embedding vectors are numbers, not text
RUN_RE =re.compile(r"[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]{6,}")


def _table_texts(conn, table: str) -> Iterator[str]:
    try:
        for (j,) in conn.execute(f"SELECT to_jsonb(t)::text FROM {table} t"):
            yield VEC_RE.sub("[vec]", j)
    except Exception:  # noqa: BLE001 - table not present in this schema version
        conn.rollback() if not conn.autocommit else None


def _blob_runs(b: bytes | memoryview | None) -> Iterator[str]:
    if not b:
        return
    text = bytes(b).decode("utf-8", errors="ignore")
    yield from RUN_RE.findall(text)


def _leaves(text: str) -> Iterator[str]:
    """Unique string leaves of a JSON text (checkpoint states repeat the same strings at every step); non-JSON text is one leaf."""
    try:
        obj = json.loads(text)
    except ValueError:
        yield text
        return
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, str):
            if len(o) >= 6:
                yield o
        elif isinstance(o, dict):
            stack += list(o.keys()) + list(o.values())
        elif isinstance(o, list):
            stack += o


def _uniq(frags) -> list[str]:
    return sorted({leaf for f in frags for leaf in _leaves(f)})


def gather(valkey_url: str | None = None) -> dict[str, list[str]]:
    """store name -> list of text fragments."""
    out: dict[str, list[str]] = {}
    with db.connect(autocommit=True) as c:
        for t in TEXT_TABLES:
            out[f"db.{t}"] = _uniq(_table_texts(c, t))
        out["db.checkpoints"] = _uniq([VEC_RE.sub("[vec]", j) for (j,) in c.execute("SELECT checkpoint::text FROM checkpoints")]
                                      + [j for (j,) in c.execute("SELECT metadata::text FROM checkpoints")])
        out["db.checkpoint_blobs"] = sorted({r for (b,) in c.execute("SELECT blob FROM checkpoint_blobs") for r in _blob_runs(b)})
        out["db.checkpoint_writes"] = sorted({r for (b,) in c.execute("SELECT blob FROM checkpoint_writes") for r in _blob_runs(b)})
    import os

    import valkey
    v = valkey.Valkey.from_url(valkey_url or os.environ.get("A7_VALKEY_URL", "redis://localhost:56379/0"), decode_responses=True)
    vals: list[str] = []
    for k in v.scan_iter("*", count=500):
        vals.append(k)
        t = v.type(k)
        if t == "string":
            vals.append(v.get(k) or "")
        elif t == "hash":
            vals += [f"{a} {b}" for a, b in v.hgetall(k).items()]
        elif t == "list":
            vals += v.lrange(k, 0, -1)
        elif t == "set":
            vals += list(v.smembers(k))
        elif t == "zset":
            vals += [m for m, _ in v.zrange(k, 0, -1, withscores=True)]
    out["valkey"] = vals
    return out


def canaries(record_texts: list[str]) -> dict[str, str]:
    """value -> type for PII VALUES found in raw source text (regex + legacy layers; names excluded). In memory only."""
    can: dict[str, str] = {}
    for t in record_texts:
        for h in scan(t, presidio=False, ner=False):
            val = t[h.start:h.end]
            if len(val) >= 6:
                can[val] = h.type
    return can


def raw_strings(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from raw_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from raw_strings(v)


def scan_stores(stores: dict[str, list[str]], can: dict[str, str]) -> dict[str, Any]:
    by_store: dict[str, Counter] = {}
    canary_hits: Counter = Counter()
    n_ids: Counter = Counter()
    n_frag = {}
    for name, frags in stores.items():
        n_frag[name] = len(frags)
        cnt: Counter = Counter()
        for t in frags:
            n_ids[name] += len(ID_SPAN_RE.findall(t))      # LangGraph checkpoint / run ids: masked inside scan() by scanner.mask_ids
            for h in scan(t, presidio=True, ner=False):
                cnt[h.type] += 1
            for val, typ in can.items():
                if val in t:
                    canary_hits[f"{name}:{typ}"] += 1
        by_store[name] = cnt
    return {"fragments": n_frag, "scanner_hits": {k: dict(v) for k, v in by_store.items() if v},
            "canary_hits": dict(canary_hits), "id_spans_masked": dict(n_ids), "canaries_checked": len(can),
            "leaks": sum(sum(v.values()) for v in by_store.values()) + sum(canary_hits.values())}
