"""Reviewer API: pending approvals (HTML queue), ticket detail, decision -> resumes the graph.

Auth: `Authorization: Bearer $A7_REVIEWER_TOKEN` on everything except /health (no token configured = all 401).
Tenant scope: every request names ONE tenant (header X-Tenant-Id or ?tenant=). Reads and writes run under RLS for that
tenant, so another tenant's ticket is a 404, never a leak (the May-2026 incident was two tenants in one session).
Only masked text is stored or shown.

Run:  uvicorn solution.a7.api.app:app --port 18082
"""
from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from .. import db
from ..nodes.human_approval import validate_decision

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class Decision(BaseModel):
    status: str
    reviewer: str
    edited_draft: str | None = None


def default_graph_factory(checkpointer):
    from ..graph import build_graph          # Builder A's module; imported lazily so queue/health work without it
    return build_graph(checkpointer)


def create_app(*, graph_factory: Callable[[Any], Any] | None = None, token: str | None = None, dsn: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = ConnectionPool(dsn or db.app_dsn(), min_size=1, max_size=8, open=False,
                              kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0})
        pool.open()
        app.state.pool = pool
        app.state.graph = None
        yield
        pool.close()

    app = FastAPI(title="PayNest A7 reviewer API", lifespan=lifespan)
    app.state.graph_factory = graph_factory or default_graph_factory
    app.state.token = token

    def auth(authorization: str | None = Header(default=None)) -> None:
        expected = app.state.token if app.state.token is not None else os.environ.get("A7_REVIEWER_TOKEN", "")
        given = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else ""
        if not expected or not secrets.compare_digest(given.encode(), expected.encode()):
            raise HTTPException(401, "invalid or missing bearer token", headers={"WWW-Authenticate": "Bearer"})

    def tenant_of(x_tenant_id: str | None = Header(default=None), tenant: str | None = None) -> str:
        t = x_tenant_id or tenant
        if not t:
            raise HTTPException(400, "tenant required: header X-Tenant-Id or ?tenant=")
        return t

    def graph(request: Request):
        if request.app.state.graph is None:
            from langgraph.checkpoint.postgres import PostgresSaver
            saver = PostgresSaver(request.app.state.pool)      # pool conns are autocommit + dict_row
            request.app.state.graph = request.app.state.graph_factory(saver)
        return request.app.state.graph

    def fetch(pool, tenant_id: str, sql: str, params: tuple = ()):
        with pool.connection() as conn, conn.transaction():
            conn.execute("SELECT a7.set_tenant(%s, true)", (tenant_id,))
            return conn.execute(sql, params).fetchall()

    @app.get("/health")
    def health(request: Request):
        with request.app.state.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, _: None = Depends(auth), tenant_id: str = Depends(tenant_of)):
        rows = fetch(request.app.state.pool, tenant_id,
                     "SELECT a.ticket_id, a.lane, a.priority, a.category, a.requested_at, a.draft_masked, "
                     "(SELECT min(due_at) FROM escalations e WHERE e.ticket_id = a.ticket_id AND e.fired_at IS NULL "
                     " AND e.resolved_at IS NULL) AS next_due FROM approvals a WHERE a.status = 'pending' "
                     "ORDER BY (a.priority = 'P1') DESC, a.requested_at")
        return TEMPLATES.TemplateResponse(request, "queue.html", {"rows": rows, "tenant": tenant_id})

    @app.get("/tickets/{ticket_id}")
    def ticket(ticket_id: str, request: Request, _: None = Depends(auth), tenant_id: str = Depends(tenant_of)):
        rows = fetch(request.app.state.pool, tenant_id,
                     "SELECT ticket_id, tenant_id, lane, priority, category, status, draft_masked, edited_draft_masked, "
                     "reviewer, requested_at, decided_at FROM approvals WHERE ticket_id = %s", (ticket_id,))
        if not rows:
            raise HTTPException(404, "ticket not found")
        esc = fetch(request.app.state.pool, tenant_id,
                    "SELECT kind, level, due_at, fired_at, resolved_at FROM escalations WHERE ticket_id = %s ORDER BY due_at",
                    (ticket_id,))
        return JSONResponse(content=_jsonable({**rows[0], "escalations": esc}))

    @app.post("/tickets/{ticket_id}/decision")
    def decide(ticket_id: str, body: Decision, request: Request, _: None = Depends(auth),
               tenant_id: str = Depends(tenant_of)):
        try:
            d = validate_decision(body.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e))
        pool = request.app.state.pool
        claimed = None
        with pool.connection() as conn, conn.transaction():          # claim: only one reviewer wins
            conn.execute("SELECT a7.set_tenant(%s, true)", (tenant_id,))
            exists = conn.execute("SELECT status FROM approvals WHERE ticket_id = %s", (ticket_id,)).fetchone()
            if not exists:
                raise HTTPException(404, "ticket not found")
            claimed = conn.execute("UPDATE approvals SET status = 'deciding' WHERE ticket_id = %s AND status = 'pending' "
                                   "RETURNING ticket_id", (ticket_id,)).fetchone()
        if not claimed:
            raise HTTPException(409, f"ticket already {exists['status']}")
        from langgraph.types import Command
        try:
            out = graph(request).invoke(Command(resume=d), {"configurable": {"thread_id": ticket_id}}, durability="sync")
        except Exception:
            with pool.connection() as conn, conn.transaction():      # release the claim so it can be retried
                conn.execute("SELECT a7.set_tenant(%s, true)", (tenant_id,))
                conn.execute("UPDATE approvals SET status = 'pending' WHERE ticket_id = %s AND status = 'deciding'", (ticket_id,))
            raise
        actions = (out or {}).get("actions") or []
        return {"ticket_id": ticket_id, "status": d["status"],
                "actions": [{k: a.get(k) for k in ("type", "status", "outbox_id")} for a in actions]}

    return app


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o.isoformat() if hasattr(o, "isoformat") else o


app = create_app()
