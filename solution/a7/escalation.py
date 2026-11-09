"""Escalation timers (sourced, SLA 3.1 + ESC) with a DB-polled runner.

Timer table (elapsed calendar hours from the ticket's received_at; a human decision stops all but the privacy one):
  l1_to_l2             P1 -> 1 h;  otherwise 4 h                         (only for lanes that wait for a human)
  privacy_dpo_security privacy/PII-exposure concern -> DPO + Security within 1 h
  p1_first_response    P1 only. Enterprise 1 h, Business 2 h; halved in payroll week (Enterprise 30 min).
                       Standard tier is best effort: no timer.

Entry points that share one firing path (`fire_escalation`):
  run_due(now)          local dev poller (call it from a loop / cron / a test with a fake clock)
  handler(event, ctx)   what an AWS EventBridge Scheduler target would call:  {"escalation_id": 12}
Firing = set fired_at, write an audit_log row, write an outbox kind='notify' row. Exactly once per escalation.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .human_db import maybe_conn, scoped

UTC = dt.timezone.utc
HOUR = dt.timedelta(hours=1)

P1_FIRST_RESPONSE = {"enterprise": 60, "business": 120}      # minutes, SLA 3.1; Standard = best effort
HUMAN_LANES = {"draft_for_agent", "human_review", "incident"}


def _parse(ts: Any, default: dt.datetime) -> dt.datetime:
    if isinstance(ts, dt.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    if isinstance(ts, str) and ts:
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return default


def is_privacy_concern(state: dict) -> bool:
    if state.get("privacy_concern"):
        return True
    return any(("privacy" in str(r).lower() or "pii_exposure" in str(r).lower()) for r in state.get("route_reasons") or [])


def timers_for(state: dict, tier: str | None, now: dt.datetime | None = None) -> list[dict]:
    """Pure: the timers a ticket gets. Each: kind, level, due_at, reason."""
    now = now or dt.datetime.now(UTC)
    base = _parse(state.get("received_at"), now)
    p1 = str(state.get("priority", "")).upper() == "P1"
    out: list[dict] = []
    if state.get("lane") in HUMAN_LANES:
        h = 1 if p1 else 4
        out.append(dict(kind="l1_to_l2", level="L2", due_at=base + h * HOUR,
                        reason=f"L1 to L2 within {h} h ({'P1' if p1 else 'not P1'})"))
    if is_privacy_concern(state):
        out.append(dict(kind="privacy_dpo_security", level="DPO+Security", due_at=base + HOUR,
                        reason="privacy or security concern: notify DPO and Security within 1 h"))
    minutes = P1_FIRST_RESPONSE.get((tier or "").lower())
    if p1 and minutes:
        payroll = bool(state.get("is_payroll_window"))
        if payroll:
            minutes //= 2
        out.append(dict(kind="p1_first_response", level="first_response", due_at=base + dt.timedelta(minutes=minutes),
                        reason=f"P1 first response {minutes} min ({tier}{', payroll week halved' if payroll else ''})"))
    return out


def create_timers(conn: psycopg.Connection, state: dict, now: dt.datetime | None = None) -> list[dict]:
    """Idempotent (UNIQUE ticket_id+kind, ON CONFLICT DO NOTHING). Returns the timers computed."""
    tenant, tid = state["tenant_id"], state["ticket_id"]
    with scoped(conn, tenant):
        row = conn.execute("SELECT tier FROM customers WHERE customer_id = %s", (tenant,)).fetchone()
        timers = timers_for(state, row[0] if row else None, now)
        for t in timers:
            conn.execute(
                "INSERT INTO escalations (tenant_id, ticket_id, kind, level, due_at, reason) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (ticket_id, kind) DO NOTHING",
                (tenant, tid, t["kind"], t["level"], t["due_at"], t["reason"]))
    return timers


def resolve_timers(conn: psycopg.Connection, tenant_id: str, ticket_id: str, now: dt.datetime | None = None) -> None:
    """A human acted: stop the not-yet-fired clocks (the privacy notification still goes out)."""
    with scoped(conn, tenant_id):
        conn.execute("UPDATE escalations SET resolved_at = %s WHERE ticket_id = %s AND fired_at IS NULL "
                     "AND resolved_at IS NULL AND kind <> 'privacy_dpo_security'",
                     (now or dt.datetime.now(UTC), ticket_id))


def _fire_locked(conn: psycopg.Connection, esc_id: int, now: dt.datetime) -> bool:
    """Inside a transaction with app.runner set. True if this call fired it."""
    row = conn.execute(
        "SELECT tenant_id, ticket_id, kind, level, due_at, reason FROM escalations "
        "WHERE id = %s AND fired_at IS NULL AND resolved_at IS NULL AND due_at <= %s FOR UPDATE SKIP LOCKED",
        (esc_id, now)).fetchone()
    if not row:
        return False
    tenant, ticket, kind, level, due_at, reason = row
    conn.execute("UPDATE escalations SET fired_at = %s WHERE id = %s", (now, esc_id))
    conn.execute("SELECT a7.set_tenant(%s, true)", (tenant,))            # audit/outbox are tenant-scoped
    body = f"ESCALATION {kind} -> {level}: {reason}. Ticket {ticket}, due {due_at.isoformat()}."
    conn.execute(
        "INSERT INTO audit_log (tenant_id, ticket_id, event, masked_text, signals, node, dedupe_key) "
        "VALUES (%s,%s,'escalation_fired',%s,%s,'escalation',%s) ON CONFLICT (dedupe_key) DO NOTHING",
        (tenant, ticket, body, Jsonb({"kind": kind, "level": level, "due_at": due_at.isoformat()}), f"esc:{esc_id}"))
    conn.execute(
        "INSERT INTO outbox (tenant_id, ticket_id, channel, masked_body, status, kind, payload, idempotency_key) "
        "VALUES (%s,%s,%s,%s,'queued','notify',%s,%s) ON CONFLICT (idempotency_key) DO NOTHING",
        (tenant, ticket, level, body, Jsonb({"escalation_id": esc_id, "kind": kind}), f"esc:{esc_id}"))
    return True


def fire_escalation(escalation_id: int, now: dt.datetime | None = None, conn: psycopg.Connection | None = None) -> bool:
    """The single firing path: a Scheduler target calls this with one id; `run_due` loops over due ids."""
    now = now or dt.datetime.now(UTC)
    with maybe_conn(conn) as c, c.transaction():
        c.execute("SELECT set_config('app.runner', 'escalation', true)")
        return _fire_locked(c, escalation_id, now)


def run_due(now: dt.datetime | None = None, conn: psycopg.Connection | None = None) -> list[int]:
    """Fire every escalation due at `now` (fake-clock friendly). Returns the ids fired; re-running fires none."""
    now = now or dt.datetime.now(UTC)
    fired: list[int] = []
    with maybe_conn(conn) as c:
        with c.transaction():
            c.execute("SELECT set_config('app.runner', 'escalation', true)")
            ids = [r[0] for r in c.execute(
                "SELECT id FROM escalations WHERE fired_at IS NULL AND resolved_at IS NULL AND due_at <= %s "
                "ORDER BY due_at, id", (now,)).fetchall()]
        for i in ids:                                  # one transaction per escalation: a failure affects only that row
            if fire_escalation(i, now, c):
                fired.append(i)
    return fired


def handler(event: dict, context: Any = None) -> dict:
    """AWS EventBridge Scheduler / Lambda entry point (Phase 6 wires it). event: {"escalation_id": int}."""
    if "escalation_id" in event:
        return {"fired": [event["escalation_id"]] if fire_escalation(int(event["escalation_id"])) else []}
    return {"fired": run_due()}


if __name__ == "__main__":      # local dev: python -m solution.a7.escalation   (poll once)
    print("fired:", run_due())
