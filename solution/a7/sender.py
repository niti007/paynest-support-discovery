"""Send-time PII restore check: the only code path that opens the vault (runs as the act task role).

    python -m solution.a7.sender --tenant C-1004 --ticket PN-100402

Reads the queued draft rows for ONE ticket (tenant-scoped, app_user), renders them through act.render_outbound over the
a7_act login + KMS Decrypt, and prints COUNTS ONLY (placeholders restored, characters). Revealed values are never printed,
logged or stored. Nothing is sent: this is the seam a real channel sender would use.
"""
from __future__ import annotations

import argparse

from .human_db import connect_app, scoped
from .nodes.act import make_vault, render_outbound
from .pii.patterns import PLACEHOLDER_RE


def check(tenant: str, ticket: str) -> dict:
    with connect_app() as conn, scoped(conn, tenant):
        rows = conn.execute("SELECT id, masked_body, payload FROM outbox WHERE ticket_id = %s AND restore_pii "
                            "AND kind = 'draft'", (ticket,)).fetchall()
    vault = make_vault()
    try:
        out = []
        for oid, body, payload in rows:
            rendered = render_outbound(body, (payload or {}).get("vault_key") or ticket, vault=vault)
            out.append({"outbox_id": oid, "placeholders": len(PLACEHOLDER_RE.findall(body)), "rendered_chars": len(rendered),
                        "still_masked": len(PLACEHOLDER_RE.findall(rendered))})
        return {"ticket": ticket, "drafts": out}
    finally:
        vault.store.conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--ticket", required=True)
    a = ap.parse_args()
    print(check(a.tenant, a.ticket))
