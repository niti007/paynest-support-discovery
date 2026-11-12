"""Box 5: dedupe_cluster (after classify, before route). Embeds the MASKED text, then per tenant:

1. Resubmit link: a ticket within LINK_DAYS before this one with cosine >= t_link -> `parent_ticket_id`.
2. Incident: >= min_cluster tickets (this one included) inside the rolling window W with cosine >= t_inc to this
   ticket -> open (or attach to) an `incidents` row and `lane_hint = "incident"`. Once a window member already
   belongs to an incident, a similar new ticket joins it even if the count is below min_cluster.
Incident wins over link (tickets of one incident are not resubmits of each other).

State read:    ticket_id, tenant_id (falls back to customer_ref), masked_text, received_at (UTC ISO), category
State written: parent_ticket_id, incident_id, lane_hint ("incident" | None), dedupe (masked/ids-only dict), trace
The node never sets `lane`; route reads `lane_hint`/`parent_ticket_id`. T_link, T_inc, W are NOT in the sources: they are
chosen on the tuning split (solution/evals/dedupe_sweep.py, report solution/evals/reports/phase4_dedupe.md).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from .. import human_db
from ..embed import embed as bedrock_embed
from ..state import TicketState

# Chosen on the tuning split; see solution/evals/reports/phase4_dedupe.md.
T_LINK = 0.85
T_INC = 0.80
WINDOW_HOURS = 2.0
LINK_DAYS = 2.0
MIN_CLUSTER = 5


@dataclass(frozen=True)
class Params:
    t_link: float = T_LINK
    t_inc: float = T_INC
    window_hours: float = WINDOW_HOURS
    link_days: float = LINK_DAYS
    min_cluster: int = MIN_CLUSTER


@dataclass(frozen=True)
class Cand:
    """A earlier ticket of the same tenant, with its cosine to the ticket under test."""
    ticket_id: str
    ts: datetime
    sim: float
    parent: str | None = None
    incident_id: int | None = None


@dataclass
class Decision:
    parent_ticket_id: str | None = None
    link_sim: float | None = None
    incident: bool = False
    incident_id: int | None = None          # existing incident to attach to (None + incident=True -> open a new one)
    cluster_ids: tuple[str, ...] = ()       # similar tickets inside the window (excluding this one)
    reasons: tuple[str, ...] = ()


def parse_ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def decide(ts: datetime, cands: Sequence[Cand], p: Params = Params()) -> Decision:
    """Pure decision from candidate tickets (same tenant, strictly earlier or equal time, not the ticket itself)."""
    win = timedelta(hours=p.window_hours)
    in_win = [c for c in cands if ts - win <= c.ts <= ts and c.sim >= p.t_inc]
    # Linked tickets still count: the first reports of an incident link to each other (cos >= t_link) before the
    # incident exists, and excluding them would stop it from ever reaching min_cluster.
    indep = in_win
    joined = next((c.incident_id for c in sorted(in_win, key=lambda c: -c.sim) if c.incident_id is not None), None)
    if joined is not None:
        return Decision(incident=True, incident_id=joined, cluster_ids=tuple(c.ticket_id for c in in_win),
                        reasons=(f"joins open incident {joined}",))
    if len(indep) + 1 >= p.min_cluster:
        return Decision(incident=True, cluster_ids=tuple(c.ticket_id for c in indep),
                        reasons=(f"{len(indep) + 1} similar tickets in {p.window_hours:g}h (cos>={p.t_inc})",))
    lw = timedelta(days=p.link_days)
    links = [c for c in cands if ts - lw <= c.ts <= ts and c.sim >= p.t_link]
    if links:
        best = max(links, key=lambda c: c.sim)
        return Decision(parent_ticket_id=best.ticket_id, link_sim=best.sim,
                        reasons=(f"resubmit of {best.ticket_id} (cos {best.sim:.3f}>={p.t_link})",))
    return Decision()


def _vec_lit(v) -> str:
    return "[" + ",".join(f"{float(x):.7f}" for x in v) + "]"


def make_dedupe_cluster(embedder: Callable[..., list[list[float]]] = bedrock_embed, *, conn=None,
                        params: Params = Params()) -> Callable[[TicketState], dict[str, Any]]:
    """Factory. `embedder(texts, input_type)`; `conn` = app_user connection (default: opens one per call)."""

    def dedupe_cluster(state: TicketState) -> dict[str, Any]:
        tid = state["ticket_id"]
        tenant = state.get("tenant_id") or state.get("customer_ref")
        trace = list(state.get("trace", []))
        if not tenant:
            trace.append({"node": "dedupe_cluster", "skipped": "no tenant"})
            return {"parent_ticket_id": None, "incident_id": None, "lane_hint": None, "dedupe": {"skipped": "no tenant"},
                    "trace": trace}
        ts = parse_ts(state["received_at"])
        vec = embedder([state["masked_text"]], "search_document")[0]
        q = _vec_lit(vec)
        horizon = ts - timedelta(days=max(params.link_days, params.window_hours / 24))
        with human_db.maybe_conn(conn) as c, human_db.scoped(c, tenant):
            rows = c.execute(
                "SELECT ticket_id, received_at, 1 - (embedding <=> %s::vector), parent_ticket_id, incident_id "
                "FROM ticket_embeddings WHERE ticket_id <> %s AND received_at BETWEEN %s AND %s",
                (q, tid, horizon, ts)).fetchall()
            cands = [Cand(r[0], r[1], float(r[2]), r[3], r[4]) for r in rows]
            d = decide(ts, cands, params)
            inc_id = d.incident_id
            if d.incident and inc_id is None:
                cat = state.get("category")
                inc_id = c.execute("INSERT INTO incidents (tenant_id, topic, ticket_ids) VALUES (%s,%s,%s) RETURNING id",
                                   (tenant, cat, [*d.cluster_ids, tid])).fetchone()[0]
                c.execute("UPDATE ticket_embeddings SET incident_id=%s, parent_ticket_id=NULL WHERE ticket_id = ANY(%s)",
                          (inc_id, list(d.cluster_ids)))
            elif d.incident:
                c.execute("UPDATE incidents SET ticket_ids = array_append(ticket_ids, %s) "
                          "WHERE id=%s AND NOT (%s = ANY(ticket_ids))", (tid, inc_id, tid))
            c.execute(
                "INSERT INTO ticket_embeddings (tenant_id, ticket_id, received_at, embedding, parent_ticket_id, incident_id) "
                "VALUES (%s,%s,%s,%s::vector,%s,%s) ON CONFLICT (tenant_id, ticket_id) DO UPDATE SET "
                "received_at=EXCLUDED.received_at, embedding=EXCLUDED.embedding, "
                "parent_ticket_id=EXCLUDED.parent_ticket_id, incident_id=EXCLUDED.incident_id",
                (tenant, tid, ts, q, d.parent_ticket_id, inc_id if d.incident else None))
        info = {"parent_ticket_id": d.parent_ticket_id, "link_sim": d.link_sim, "incident_id": inc_id if d.incident else None,
                "cluster_size": len(d.cluster_ids) + 1 if d.incident else 0, "reasons": list(d.reasons),
                "params": {"t_link": params.t_link, "t_inc": params.t_inc, "window_hours": params.window_hours}}
        trace.append({"node": "dedupe_cluster", **{k: v for k, v in info.items() if k != "params"}})
        return {"parent_ticket_id": d.parent_ticket_id, "incident_id": inc_id if d.incident else None,
                "lane_hint": "incident" if d.incident else None, "dedupe": info, "trace": trace}

    return dedupe_cluster
