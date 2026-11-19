"""Chunk KB articles and statutory rules into kb_chunks, compute effective dates, optionally embed.

CHUNKING
  * Rule: one chunk (title + old->new + effective date + source), split only if > MAX_TOKENS.
  * KB article: text = excerpt (the data room has no full article bodies; most excerpts are the stub
    "See full article in Confluence space SUP-KB.", which is dropped, leaving the title as the body).
    Split on blank-line paragraphs, then sentences for oversize paragraphs; pack greedily up to
    MAX_TOKENS (500, approximated as 1.3 x words), with the last OVERLAP_TOKENS (~40) carried into the
    next chunk. Every current article is < 100 tokens, so each yields exactly 1 chunk; the splitter
    is what real full-length articles would use.
  * Title is stored separately and is also part of the embedded text and the tsvector.
  * Language: script detection (Devanagari -> hi, Arabic -> ar, else en). All current content is en.

EFFECTIVE DATES (no such fields in the data; derived here)
  * rule chunk: effective_from = rule.effective_from; effective_to NULL.
  * KB chunk: effective_from NULL; last_reviewed = article.last_reviewed.
  * A KB article is SUPERSEDED by a rule when ALL hold:
      - article.topic in rule.affects_kb_topics            (topic)
      - rule.region == article.region or article.region == 'ALL'   (region)
      - article.last_reviewed < rule.effective_from        (date: not reviewed after the change)
    Then superseded_by = rule_id and effective_to = rule.effective_from - 1 day.
    If several rules match, the earliest-effective one wins (the article became invalid first).
  * OVERRIDES: explicit (article_id -> rule_id | None) table for manual corrections; None forces
    "not superseded". Currently empty because the derived logic gives the expected 4 links.
    Expected: KB-0201/KB-0202 -> R-UAE-WPS-2026-07 (to 2026-07-14), KB-0142/KB-0143 -> R-IN-PF-2026-08 (to 2026-07-31).
    KB-0150 (tds) and KB-0160 (gratuity) were reviewed after their rules, so they stay valid.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from solution.a7.db import connect

DR = Path(__file__).resolve().parents[2] / "data-room"
MAX_TOKENS = 500
OVERLAP_TOKENS = 40
STUB = "See full article in Confluence space SUP-KB."
OVERRIDES: dict[str, str | None] = {}


def tokens(text: str) -> int:
    return int(len(text.split()) * 1.3) + 1


def detect_language(text: str) -> str:
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    if re.search(r"[؀-ۿ]", text):
        return "ar"
    return "en"


def split_text(text: str, max_tokens: int = MAX_TOKENS, overlap: int = OVERLAP_TOKENS) -> list[str]:
    """Paragraph/sentence packing with a small tail overlap."""
    units: list[str] = []
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        if tokens(para) <= max_tokens:
            units.append(para)
        else:
            units.extend(s for s in re.split(r"(?<=[.!?])\s+", para) if s)
    chunks: list[str] = []
    cur: list[str] = []
    for u in units:
        if cur and tokens(" ".join(cur + [u])) > max_tokens:
            chunks.append(" ".join(cur))
            tail: list[str] = []
            for w in reversed(" ".join(cur).split()):
                tail.insert(0, w)
                if tokens(" ".join(tail)) >= overlap:
                    break
            cur = [" ".join(tail)]
        cur.append(u)
    if cur:
        chunks.append(" ".join(cur))
    return chunks or [text]


def find_supersede(article: dict, rules: list[dict]) -> dict | None:
    aid = article["id"]
    by_id = {r["rule_id"]: r for r in rules}
    if aid in OVERRIDES:
        return by_id.get(OVERRIDES[aid]) if OVERRIDES[aid] else None
    hits = [r for r in rules
            if article["topic"] in r["affects_kb_topics"]                       # topic
            and (r["region"] == article["region"] or article["region"] == "ALL")  # region
            and article["last_reviewed"] < r["effective_from"]]                  # date
    return min(hits, key=lambda r: r["effective_from"]) if hits else None


def build_chunks() -> tuple[list[dict], dict]:
    articles = json.load(open(DR / "data/kb_articles.json", encoding="utf-8"))
    rules = json.load(open(DR / "integrations/statutory_rules.json", encoding="utf-8"))["rules"]
    chunks: list[dict] = []
    for a in articles:
        rule = find_supersede(a, rules)
        eto = (date.fromisoformat(rule["effective_from"]) - timedelta(days=1)) if rule else None
        excerpt = (a.get("excerpt") or "").replace(STUB, "").strip()
        body = excerpt or a["title"]
        for i, part in enumerate(split_text(body)):
            chunks.append(dict(source="kb", source_id=a["id"], chunk_index=i, region=a["region"],
                               language=detect_language(a["title"] + part), topic=a["topic"], title=a["title"],
                               body=part, last_reviewed=a["last_reviewed"], effective_from=None, effective_to=eto,
                               superseded_by=rule["rule_id"] if rule else None))
    for r in rules:
        text = (f"{r['title']}. Effective {r['effective_from']}. Was: {r['old_value']}. Now: {r['new_value']}. "
                f"Source: {r['source']}. Status: {r['status']}.")
        for i, part in enumerate(split_text(text)):
            chunks.append(dict(source="rule", source_id=r["rule_id"], chunk_index=i, region=r["region"],
                               language=detect_language(text), topic=r["topic"], title=r["title"], body=part,
                               last_reviewed=None, effective_from=r["effective_from"], effective_to=None,
                               superseded_by=None))
    return chunks, {"articles": len(articles), "rules": len(rules)}


def embed_text(c: dict) -> str:
    return c["title"] if c["body"] == c["title"] else f"{c['title']}\n{c['body']}"


UPSERT = """
INSERT INTO kb_chunks(source,source_id,chunk_index,region,language,tenant_scope,topic,title,body,embedding,
                      last_reviewed,effective_from,effective_to,superseded_by)
VALUES (%(source)s,%(source_id)s,%(chunk_index)s,%(region)s,%(language)s,NULL,%(topic)s,%(title)s,%(body)s,
        %(emb)s::vector,%(last_reviewed)s,%(effective_from)s,%(effective_to)s,%(superseded_by)s)
ON CONFLICT (source,source_id,chunk_index) DO UPDATE SET
  region=EXCLUDED.region, language=EXCLUDED.language, topic=EXCLUDED.topic, title=EXCLUDED.title,
  embedding = CASE WHEN EXCLUDED.embedding IS NOT NULL THEN EXCLUDED.embedding
                   WHEN kb_chunks.title IS NOT DISTINCT FROM EXCLUDED.title AND kb_chunks.body = EXCLUDED.body
                   THEN kb_chunks.embedding ELSE NULL END,
  body=EXCLUDED.body, last_reviewed=EXCLUDED.last_reviewed, effective_from=EXCLUDED.effective_from,
  effective_to=EXCLUDED.effective_to, superseded_by=EXCLUDED.superseded_by
"""


def load_kb(conn, embedder=None) -> dict:
    """embedder: callable(texts, input_type) -> vectors, or None for --no-embed."""
    chunks, counts = build_chunks()
    vecs = embedder([embed_text(c) for c in chunks], "search_document") if embedder else [None] * len(chunks)
    for c, v in zip(chunks, vecs):
        c["emb"] = "[" + ",".join(f"{x:.7f}" for x in v) + "]" if v is not None else None
        conn.execute(UPSERT, c)
    conn.commit()
    counts.update(chunks=len(chunks), superseded_chunks=sum(1 for c in chunks if c["superseded_by"]),
                  embedded=len(chunks) if embedder else 0)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--embed", dest="embed", action="store_true", default=True, help="embed via Bedrock (default)")
    g.add_argument("--no-embed", dest="embed", action="store_false", help="skip embeddings (offline)")
    args = ap.parse_args()
    embedder = None
    if args.embed:
        from solution.a7.embed import embed
        embedder = embed
    with connect() as conn:
        counts = load_kb(conn, embedder)
        for k, v in counts.items():
            print(f"{k}: {v}")
        print("db.kb_chunks:", conn.execute("SELECT count(*) FROM kb_chunks").fetchone()[0])
        print("db.kb_chunks with embedding:", conn.execute("SELECT count(*) FROM kb_chunks WHERE embedding IS NOT NULL").fetchone()[0])


if __name__ == "__main__":
    sys.exit(main())
