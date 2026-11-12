"""Living Langfuse dataset built from APPROVED drafts.

Source of truth: the `approvals` table (status approved / edited; draft_masked, or edited_draft_masked when the reviewer
edited it), and the masked ticket text from the LangGraph checkpoint. Re-running adds only
tickets that are not in the dataset yet (item id = "a7-<ticket_id>"), so the dataset grows as reviewers approve. Everything is masked
(input/expected_output go through tracing.mask_data as well as the pii_gate masking they already have).

    uv run python -m solution.evals.langfuse_dataset            (needs LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY)
"""
from __future__ import annotations

from typing import Any

from solution.a7 import db, tracing

DATASET = "a7-approved-drafts"


def approved_drafts(conn=None) -> list[dict[str, Any]]:
    from solution.a7.graph import postgres_checkpointer, thread_config

    own = conn is None
    conn = conn or db.connect(autocommit=True)
    try:
        rows = conn.execute(
            "SELECT ticket_id, tenant_id, status, reviewer, COALESCE(edited_draft_masked, draft_masked), lane FROM approvals "
            "WHERE status IN ('approved','edited') AND COALESCE(edited_draft_masked, draft_masked) IS NOT NULL ORDER BY ticket_id").fetchall()
    finally:
        if own:
            conn.close()
    out = []
    with postgres_checkpointer() as cp:
        for tid, tenant, status, reviewer, body, lane in rows:
            tup = cp.get_tuple(thread_config(tid))
            vals = (tup.checkpoint.get("channel_values") if tup else None) or {}
            out.append({"ticket_id": tid, "tenant": tenant, "status": status, "reviewer": reviewer, "draft": body, "lane": lane,
                        "masked_text": vals.get("masked_text") or "", "language": vals.get("language"), "category": vals.get("category"),
                        "citations": [c.get("source_id") for c in vals.get("citations") or []], "as_of": vals.get("as_of")})
    return out


def sync(client: Any | None = None, items: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """Create the dataset if needed and add the approved drafts that are not in it yet. Returns counts."""
    from solution.evals import judges

    lf = client or tracing.get_client()
    items = approved_drafts() if items is None else items
    try:
        lf.create_dataset(name=DATASET, description="Reviewer-approved A7 drafts (masked). Grows as reviewers approve.",
                          metadata={"source": "approvals+outbox", "masked": True})
    except Exception:  # noqa: BLE001 - already exists
        pass
    try:
        have = {i.id for i in lf.get_dataset(DATASET).items}
    except Exception:  # noqa: BLE001
        have = set()
    added = 0
    for it in items:
        iid = f"a7-{it['ticket_id']}"
        if iid in have:
            continue
        judges.assert_masked(it["masked_text"], it["draft"])
        lf.create_dataset_item(
            dataset_name=DATASET, id=iid,
            input=tracing.mask_data({"ticket": it["masked_text"], "language": it["language"], "category": it["category"], "as_of": it["as_of"]}),
            expected_output=tracing.mask_data({"draft": it["draft"], "citations": it["citations"]}),
            metadata={"ticket_id": it["ticket_id"], "tenant": it["tenant"], "lane": it["lane"], "approval": it["status"], "reviewer": it["reviewer"]})
        added += 1
    lf.flush()
    return {"approved": len(items), "already_in_dataset": len(have), "added": added}


if __name__ == "__main__":
    print(sync())
