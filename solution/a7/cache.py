"""Box 7: cache_lookup. Approved-answer semantic cache (pgvector) + status/tool cache (Valkey).

Approved-answer cache (table answer_cache)
  * Written ONLY by `store_approved` after a human approved (or edited) a draft; called from act.
  * Tenant-scoped: tenant_id is part of the key AND of the RLS policy. Tenant X never reads tenant Y's rows.
  * Hit = cosine >= HIT_THRESHOLD (0.92) AND same topic AND same region AND not expired (TTL 7 days).
  * Never cached: P1, statutory (category / rule citation / statutory lane reasons), drafts with tenant-specific
    figures (numeric check), drafts with PII placeholders or anything the PII scanner finds, non-How-To.
  * Topic flush: a trigger on kb_chunks deletes the topic's rows when KB content changes (schema.sql);
    `flush_topic` / `flush_stale` do the same from Python (load_kb re-run).

Status/tool cache (Valkey, 30 s)
  * Key = a7:tool:{tenant}:{tool}:{sha256(canonical args)[:32]}; value = the MASKED tool output as JSON.
  * Nothing PII in keys (args are hashed) and values pass through masking.mask_obj.

Sourced vs ours:
  sourced  - approved-answers-only semantic cache, topic flush, 30 s status cache, never P1 statutory / tenant figures
             (Session 4 p.7; plan box 7).
  ours     - cosine 0.92, 7-day TTL, same-region rule, numeric-check definition, Valkey key shape.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from psycopg.types.json import Jsonb

from .pii.scanner import scan

HIT_THRESHOLD = 0.92          # ours (plan Phase 4 decisions)
TTL_DAYS = 7                  # ours; the sources specify no TTL for the approved-answer cache
STATUS_TTL_S = 30             # sourced: S4 p.7, plan box 7
CACHEABLE_CATEGORY = "howto"  # sourced: the semantic cache holds approved How-To answers (S4 p.7)
# A human-approved draft arrives through draft_for_agent; a reviewer-graded shadow auto_answer draft and a reviewer-approved
# human_review How-To are approved too (user decision 2026-11-12). Every never-cache rule below still applies to all three.
CACHEABLE_LANES = {"draft_for_agent", "auto_answer", "human_review"}
APPROVED = {"approved", "edited"}

UTC = timezone.utc
PLACEHOLDER = re.compile(r"<[A-Z_]+_\d+>|\[[A-Z_]{3,}\]")
_IDS = re.compile(r"\b(?:KB|PN|PNJ|R|C)-[A-Za-z0-9-]+\b")
_MONEY = re.compile(r"(?:₹|\$|€|\bAED\b|\bINR\b|\bUSD\b|\bRs\.?)\s*\d|\d\s*(?:%|AED\b|INR\b|USD\b|lakh|crore)", re.I)
_DATE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|"
                   r"\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_BIGNUM = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{2,3})+(?![\w])|(?<![\w.])\d{3,}(?![\w])|\b\d+\.\d+\b")


def has_tenant_figures(text: str) -> bool:
    """Numeric check (ours): True when the text carries amounts, percentages, dates, decimals or any number of
    3+ digits. Ids (KB-0105, PN-..., R-...) are removed first; small integers (step numbers, "within 24 hours")
    are allowed."""
    t = _IDS.sub(" ", text or "")
    return bool(_MONEY.search(t) or _DATE.search(t) or _BIGNUM.search(t))


def _cite_id(c: Any) -> str:
    """Graph citations are dicts ({"source_id": ...}); older callers pass plain ids."""
    return str(c.get("source_id", "")) if isinstance(c, dict) else str(c)


def never_cache_reason(*, priority: str | None, category: str | None, lane: str | None, citations: list | None,
                       route_reasons: list | None, draft: str | None, approval_status: str | None,
                       guard: Any = None, is_injection: bool = False) -> str | None:
    """None = cacheable, else the reason it must not be cached."""
    if approval_status not in APPROVED:
        return "not human-approved"
    if is_injection:
        return "injection flagged"
    if str(priority or "").upper() == "P1":
        return "P1"
    if category != CACHEABLE_CATEGORY:
        return "not How-To" if category != "statutory" else "statutory"
    if lane not in CACHEABLE_LANES:
        return f"lane {lane}"
    if any(_cite_id(c).startswith("R-") for c in (citations or [])) or any("statutory" in str(r) or "rule:" in str(r) for r in (route_reasons or [])):
        return "statutory (rule citation)"
    if isinstance(guard, dict) and (guard.get("ok") is False or guard.get("passed") is False):
        return "output_guard did not pass"
    if not draft or not draft.strip():
        return "empty draft"
    if PLACEHOLDER.search(draft) or scan(draft, presidio=False):
        return "PII or placeholder in draft"
    if has_tenant_figures(draft):
        return "tenant-specific figures"
    return None


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def lookup(conn, *, tenant_id: str, topic: str | None, region: str | None, embedding: list[float],
           now: datetime | None = None, threshold: float = HIT_THRESHOLD) -> dict | None:
    """Best live approved answer for this tenant with same topic+region and cosine >= threshold, else None.
    The tenant filter is explicit (belt) and RLS enforces it again (braces)."""
    if not (tenant_id and topic and region):
        return None
    row = conn.execute(
        "SELECT id, answer, citations, source_ticket_id, 1 - (embedding <=> %s::vector) AS sim "
        "FROM answer_cache WHERE tenant_id = %s AND topic = %s AND region = %s AND approved "
        "AND (expires_at IS NULL OR expires_at > %s) ORDER BY embedding <=> %s::vector LIMIT 1",
        (_vec(embedding), tenant_id, topic, region, _now(now), _vec(embedding))).fetchone()
    if not row or row[4] < threshold:
        return None
    return {"id": row[0], "answer": row[1], "citations": row[2], "source_ticket_id": row[3], "similarity": float(row[4])}


def store_approved(conn, state: dict, embedding: list[float], *, now: datetime | None = None,
                   ttl_days: int = TTL_DAYS) -> tuple[bool, str | None]:
    """Cache a human-approved draft. Returns (stored, reason_if_not). Caller passes a tenant-scoped connection."""
    ap = state.get("approval") or {}
    final = ap.get("edited_draft") if ap.get("status") == "edited" and ap.get("edited_draft") else state.get("draft")
    reason = never_cache_reason(priority=state.get("priority"), category=state.get("category"), lane=state.get("lane"),
                                citations=state.get("citations"), route_reasons=state.get("route_reasons"), draft=final,
                                approval_status=ap.get("status"), guard=state.get("guard"),
                                is_injection=bool(state.get("is_injection")))
    topic, region, question = state.get("topic"), state.get("region"), state.get("masked_text")
    if reason is None and not (topic and region and question):
        reason = "missing topic/region/question"
    if reason is None and scan(question, presidio=False):       # placeholders are fine, raw values never
        reason = "PII in question"
    if reason:
        return False, reason
    t = _now(now)
    conn.execute(
        "INSERT INTO answer_cache (tenant_id, topic, region, question, answer, citations, embedding, approved, source_ticket_id, "
        "approved_by, created_at, expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s::vector,true,%s,%s,%s,%s) "
        "ON CONFLICT (tenant_id, source_ticket_id) DO NOTHING",
        (state["tenant_id"], topic, region, question, final, Jsonb(state.get("citations") or []), _vec(embedding),
         state.get("ticket_id"), ap.get("reviewer"), t, t + timedelta(days=ttl_days)))
    return True, None


def flush_topic(conn, topic: str) -> int:
    """Delete every tenant's cached answers for `topic` (use an owner connection or the DB function)."""
    n = conn.execute("SELECT count(*) FROM answer_cache WHERE topic = %s", (topic,)).fetchone()[0]
    conn.execute("SELECT a7.flush_answer_cache(%s)", (topic,))
    return n


def flush_stale(conn) -> int:
    """Belt and braces after a KB load: drop cached answers older than their topic's newest kb_chunks.updated_at."""
    row = conn.execute("WITH d AS (DELETE FROM answer_cache c USING (SELECT topic, max(updated_at) AS u FROM kb_chunks GROUP BY topic) k "
                       "WHERE c.topic = k.topic AND c.created_at < k.u RETURNING 1) SELECT count(*) FROM d").fetchone()
    return row[0]


def purge_expired(conn, now: datetime | None = None) -> int:
    return conn.execute("DELETE FROM answer_cache WHERE expires_at <= %s", (_now(now),)).rowcount


# ---------------------------------------------------------------- box 7 node
def make_cache_lookup(conn_factory: Callable[[], Any], embedder: Callable[..., list[list[float]]] | None = None,
                      *, now: Callable[[], datetime] | None = None):
    """Node factory. Reads state: tenant_id, masked_text, topic, region, lane, priority, category.
    Writes: cache = {status: hit|miss|skipped, similarity?, entry_id?, reason?}; on a hit also
    draft, citations, cache_hit=True, cached_from (source ticket id). Does not change `lane`: the orchestrator's
    router decides what a hit means (skip the LLM agent, still go through output_guard and human_approval)."""
    def cache_lookup(state: dict) -> dict:
        tenant, text = state.get("tenant_id"), state.get("masked_text")
        if str(state.get("priority", "")).upper() == "P1" or state.get("category") == "statutory":
            return {"cache": {"status": "skipped", "reason": "P1 or statutory is never served from cache"}}
        if not (tenant and text and state.get("topic") and state.get("region")):
            return {"cache": {"status": "skipped", "reason": "missing tenant/text/topic/region"}}
        from .human_db import scoped
        emb = (embedder or _default_embedder)([text], "search_query")[0]
        conn = conn_factory()
        try:
            with scoped(conn, tenant):
                hit = lookup(conn, tenant_id=tenant, topic=state["topic"], region=state["region"], embedding=emb,
                             now=now() if now else None)
        finally:
            conn.close()
        if not hit:
            return {"cache": {"status": "miss"}}
        return {"cache": {"status": "hit", "similarity": round(hit["similarity"], 4), "entry_id": hit["id"]},
                "cache_hit": True, "cached_from": hit["source_ticket_id"], "draft": hit["answer"],
                "citations": hit["citations"]}
    return cache_lookup


def _default_embedder(texts: list[str], input_type: str = "search_document") -> list[list[float]]:
    from .embed import embed
    return embed(texts, input_type)


# ---------------------------------------------------------------- Valkey status/tool cache
def valkey_client(url: str | None = None):
    import valkey
    return valkey.Valkey.from_url(url or os.environ.get("A7_VALKEY_URL", "redis://localhost:56379/0"), decode_responses=True)


def tool_key(tenant_id: str, tool: str, args: dict | None) -> str:
    h = hashlib.sha256(json.dumps(args or {}, sort_keys=True, default=str).encode()).hexdigest()[:32]
    return f"a7:tool:{tenant_id}:{tool}:{h}"


class ToolCache:
    """get/set masked tool output for 30 s. Args are hashed, so no PII can reach a key; values are masked again."""

    def __init__(self, client=None, ttl: int = STATUS_TTL_S):
        self.client, self.ttl = client or valkey_client(), ttl

    def get(self, tenant_id: str, tool: str, args: dict | None):
        raw = self.client.get(tool_key(tenant_id, tool, args))
        return json.loads(raw) if raw is not None else None

    def set(self, tenant_id: str, tool: str, args: dict | None, value: Any) -> None:
        from .tools.masking import mask_obj
        self.client.set(tool_key(tenant_id, tool, args), json.dumps(mask_obj(value), default=str), ex=self.ttl)

    def call(self, tenant_id: str, tool: str, args: dict | None, fn: Callable[[], Any]):
        """Cached call; only successful (non-exception) results are stored."""
        hit = self.get(tenant_id, tool, args)
        if hit is not None:
            return hit
        out = fn()
        self.set(tenant_id, tool, args, out)
        return out
