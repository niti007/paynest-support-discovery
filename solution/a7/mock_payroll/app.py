"""Read-only mock of the Ramco Payce payroll API (data-room/integrations/payroll_api_openapi.yaml).

Run:  uvicorn solution.a7.mock_payroll.app:app --port 18081
Env:  A7_PAYROLL_API_TOKEN  bearer token (default a7-mock-token-dev)

Rules
- Bearer token required (401). Every data request needs the tenant header X-Tenant-ID (400) naming a
  known tenant (403). Anything that belongs to another tenant answers 403 {"error": "forbidden"}
  with no data. Statutory rates are only served for the tenant's own region.
- Rate limit: 30 calls per rolling 60 s per (tenant, token) on all data endpoints; 429 + Retry-After.
- /statutory/rates returns the rule in force at `as_of`; with no as_of it returns TODAY's, as the
  real API does (callers must pass the ticket date).
- Mutating endpoints in the spec (POST payroll-runs, POST webhooks) answer 405: read-only mock.
- Data is deterministic and synthetic, derived from data-room (customers.csv, statutory_rules.json).
  Responses carry no personal data: employee refs are masked, payslips have no names or amounts.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, Query
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[3]
DR = ROOT / "data-room"
DEFAULT_TOKEN = "a7-mock-token-dev"
LIMIT_PER_MIN = 30
WINDOW_S = 60.0
RUN_MONTHS = ["2026-10", "2026-09", "2026-08", "2026-07", "2026-06"]  # newest first
_EMP_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_RUN_RE = re.compile(r"^RUN-(C-[0-9]{4})-([0-9]{4}-[0-9]{2})$")


def _tenants() -> dict[str, dict]:
    with open(DR / "data" / "customers.csv", encoding="utf-8", newline="") as f:
        return {r["customer_id"]: r for r in csv.DictReader(f)}


def _rules() -> list[dict]:
    return json.loads((DR / "integrations" / "statutory_rules.json").read_text())["rules"]


def run_for(tenant_id: str, month: str) -> dict:
    """Deterministic run fixture. C-1004 Aug 2026 is stuck in `computing` (the PN-101055 story)."""
    n = int(tenant_id.split("-")[1])
    if tenant_id == "C-1004" and month == "2026-08":
        state, msg = "computing", "Computing net pay: batch 14 of 20 in progress"
    elif month == "2026-10":
        state, msg = (("bank_file_generated", "Bank file generated, awaiting bank pickup") if n % 2 == 0
                      else ("awaiting_approval", "Computation complete, awaiting approver sign-off"))
    else:
        state, msg = "paid", "Payment confirmed by bank"
    y, m = map(int, month.split("-"))
    return {"run_id": f"RUN-{tenant_id}-{month}", "tenant_id": tenant_id, "month": month, "state": state,
            "last_engine_message": msg, "updated_at": f"{y:04d}-{m:02d}-28T10:00:00Z"}


def rates_as_of(region: str, as_of: date) -> list[dict]:
    """Per topic: the new value once effective_from <= as_of, else the old value (rule_id null)."""
    out = []
    for r in _rules():
        if r["region"] != region:
            continue
        if as_of >= date.fromisoformat(r["effective_from"]):
            out.append({"rule_id": r["rule_id"], "topic": r["topic"], "title": r["title"],
                        "value": r["new_value"], "effective_from": r["effective_from"], "source": r["source"]})
        else:
            out.append({"rule_id": None, "topic": r["topic"], "title": r["title"] + " (previous rule)",
                        "value": r["old_value"], "effective_from": None, "source": r["source"]})
    return out


class RateLimiter:
    """Rolling-window limiter keyed on (tenant, token)."""

    def __init__(self, limit: int = LIMIT_PER_MIN, window: float = WINDOW_S, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def check(self, key: tuple[str, str]) -> int | None:
        """None if allowed (and counted); else seconds to wait."""
        now, q = self.clock(), self.hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return max(1, math.ceil(self.window - (now - q[0])))
        q.append(now)
        return None


def _err(status: int, msg: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status, headers=headers)


def create_app(token: str | None = None, limiter: RateLimiter | None = None, today=None) -> FastAPI:
    token = token or os.environ.get("A7_PAYROLL_API_TOKEN", DEFAULT_TOKEN)
    limiter = limiter or RateLimiter()
    today = today or (lambda: datetime.now(timezone.utc).date())
    tenants = _tenants()
    app = FastAPI(title="PayNest mock Ramco Payce API (read-only)", version="2.4.1")
    app.state.limiter = limiter

    def gate(authorization, x_tenant_id):
        """Auth -> tenant header -> known tenant -> rate limit. Returns (tenant_id, error response)."""
        if authorization != f"Bearer {token}":
            return None, _err(401, "unauthorized", {"WWW-Authenticate": "Bearer"})
        if not x_tenant_id:
            return None, _err(400, "X-Tenant-ID header required")
        if x_tenant_id not in tenants:
            return None, _err(403, "forbidden")
        wait = limiter.check((x_tenant_id, token))
        if wait is not None:
            return None, _err(429, "too many requests", {"Retry-After": str(wait)})
        return x_tenant_id, None

    @app.get("/health")
    def health():
        return {"status": "ok", "read_only": True, "limit_per_min": limiter.limit}

    @app.get("/tenants/{tenant_id}")
    def get_tenant(tenant_id: str, authorization: str | None = Header(None), x_tenant_id: str | None = Header(None)):
        t, e = gate(authorization, x_tenant_id)
        if e:
            return e
        if tenant_id != t:
            return _err(403, "forbidden")
        c = tenants[t]
        return {"tenant_id": t, "company_name": c["name"], "region": c["region"],
                "contract_tier": c["tier"], "employee_count": int(c["employees_paid"])}

    @app.get("/tenants/{tenant_id}/payroll-runs")
    def list_runs(tenant_id: str, month: str | None = Query(None, pattern=r"^[0-9]{4}-[0-9]{2}$"),
                  page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                  authorization: str | None = Header(None), x_tenant_id: str | None = Header(None)):
        t, e = gate(authorization, x_tenant_id)
        if e:
            return e
        if tenant_id != t:
            return _err(403, "forbidden")
        runs = [run_for(t, m) for m in RUN_MONTHS if month in (None, m)]
        lo = (page - 1) * page_size
        return {"items": runs[lo:lo + page_size], "page": page, "page_size": page_size, "total": len(runs)}

    def _month_of(run_id: str, t: str):
        m = _RUN_RE.match(run_id)
        if not m:
            return None, _err(404, "run not found")
        if m.group(1) != t:
            return None, _err(403, "forbidden")           # another tenant's run: no data, no existence hint
        if m.group(2) not in RUN_MONTHS:
            return None, _err(404, "run not found")
        return m.group(2), None

    @app.get("/payroll-runs/{run_id}/status")
    def run_status(run_id: str, authorization: str | None = Header(None), x_tenant_id: str | None = Header(None)):
        t, e = gate(authorization, x_tenant_id)
        if e:
            return e
        month, e = _month_of(run_id, t)
        return e or run_for(t, month)

    @app.get("/payroll-runs/{run_id}/employees/{employee_id}/payslip")
    def payslip(run_id: str, employee_id: str, authorization: str | None = Header(None),
                x_tenant_id: str | None = Header(None)):
        t, e = gate(authorization, x_tenant_id)
        if e:
            return e
        month, e = _month_of(run_id, t)
        if e:
            return e
        if not _EMP_RE.match(employee_id):
            return _err(422, "invalid employee_id")
        run = run_for(t, month)
        return {"run_id": run_id, "employee_ref": "*" * max(0, len(employee_id) - 2) + employee_id[-2:],
                "period": month, "currency": "INR" if tenants[t]["region"] == "IN" else "AED",
                "status": "generated" if run["state"] in ("approved", "bank_file_generated", "paid") else "pending"}

    @app.get("/statutory/rates")
    def statutory(region: str | None = Query(None), as_of: str | None = Query(None),
                  authorization: str | None = Header(None), x_tenant_id: str | None = Header(None)):
        t, e = gate(authorization, x_tenant_id)
        if e:
            return e
        if region not in ("IN", "UAE"):
            return _err(422, "region must be IN or UAE")
        if region != tenants[t]["region"]:
            return _err(403, "forbidden")
        try:
            d = date.fromisoformat(as_of) if as_of else today()
        except ValueError:
            return _err(422, "as_of must be YYYY-MM-DD")
        return {"region": region, "as_of": d.isoformat(), "rules": rates_as_of(region, d)}

    # Mutating endpoints exist in the spec; this mock is read-only.
    @app.post("/tenants/{tenant_id}/payroll-runs")
    def create_run(tenant_id: str):
        return _err(405, "read-only mock: mutating endpoints are not available", {"Allow": "GET"})

    @app.post("/webhooks")
    def register_webhook():
        return _err(405, "read-only mock: mutating endpoints are not available", {"Allow": "GET"})

    return app


app = create_app()
