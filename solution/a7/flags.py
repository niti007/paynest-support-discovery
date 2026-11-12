"""Feature flags / kill switch.

`auto_send_enabled` (table feature_flags) is the kill switch for auto_answer. Rules:
  * Default OFF. A missing row, a DB error or no connection = OFF (fail closed).
  * Env A7_AUTO_SEND can only force OFF: a falsy value (0/false/no/off) overrides the DB flag; any other value,
    including "true", is ignored. It can never turn the flag ON. A7_KILL_SWITCH=1/true also forces OFF.
  * Only the reviewer API POST /admin/flags (admin token) writes the flag. Head of Support is our unsourced default role.
"""
from __future__ import annotations

import os

AUTO_SEND = "auto_send_enabled"
KNOWN = {AUTO_SEND}
_FALSY = {"0", "false", "no", "off"}
_TRUTHY = {"1", "true", "yes", "on"}


def env_forces_off() -> bool:
    v = os.environ.get("A7_AUTO_SEND")
    if v is not None and v.strip().lower() in _FALSY:
        return True
    return os.environ.get("A7_KILL_SWITCH", "").strip().lower() in _TRUTHY


def get_flag(conn, name: str) -> bool:
    """DB value only (no env). False on any problem."""
    try:
        row = conn.execute("SELECT enabled FROM feature_flags WHERE name = %s", (name,)).fetchone()
    except Exception:  # noqa: BLE001 - fail closed
        try:
            if conn.info.transaction_status == 3:   # INERROR
                conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False
    if not row:
        return False
    return bool(row["enabled"] if isinstance(row, dict) else row[0])   # dict_row (API pool) or tuple_row


def set_flag(conn, name: str, enabled: bool, updated_by: str) -> None:
    if name not in KNOWN:
        raise ValueError(f"unknown flag {name!r}")
    conn.execute("INSERT INTO feature_flags (name, enabled, updated_by, updated_at) VALUES (%s,%s,%s,now()) "
                 "ON CONFLICT (name) DO UPDATE SET enabled = EXCLUDED.enabled, updated_by = EXCLUDED.updated_by, updated_at = now()",
                 (name, enabled, updated_by))


def auto_send_enabled(conn=None) -> bool:
    """Effective kill-switch state: DB flag AND NOT env override. No connection = OFF."""
    if env_forces_off() or conn is None:
        return False
    return get_flag(conn, AUTO_SEND)
