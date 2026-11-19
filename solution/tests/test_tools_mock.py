"""Mock payroll API + tools. DB-free except test_search_kb_* (marked db)."""
import json
from typing import Annotated, TypedDict
from datetime import date

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from solution.a7 import tools as T
from solution.a7.mock_payroll import schemas
from solution.a7.mock_payroll.app import DR, RateLimiter, create_app
from solution.a7.pii.scanner import scan
from solution.a7.tools.client import PayrollClient

TOKEN = "t0k"
TODAY = date(2026, 11, 9)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def app(clock):
    return create_app(TOKEN, RateLimiter(clock=clock), today=lambda: TODAY)


@pytest.fixture()
def api(app):
    return TestClient(app)


def H(tenant="C-1001", token=TOKEN):
    return {"Authorization": f"Bearer {token}", "X-Tenant-ID": tenant}


@pytest.fixture()
def sleeps():
    return []


@pytest.fixture()
def wired(app, sleeps):
    """Tools talk to the mock app in-process; sleep is recorded, not slept."""
    c = PayrollClient("http://test", TOKEN, sleep=sleeps.append)
    c.http = TestClient(app, base_url="http://test")
    T.set_client(c)
    yield c
    T.set_client(None)


class _State(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    tenant_id: str
    received_at: str


def run_tool(tool, args, tenant="C-1001", received_at="2026-08-10T09:00:00Z"):
    """Run through ToolNode inside a real graph, as Builder A's agent loop does."""
    g = StateGraph(_State)
    g.add_node("tools", ToolNode([tool]))
    g.add_edge(START, "tools")
    out = g.compile().invoke({"messages": [AIMessage("", tool_calls=[{"name": tool.name, "args": args, "id": "c1"}])],
                              "tenant_id": tenant, "received_at": received_at})
    return json.loads(out["messages"][-1].content), out["messages"]


# ---------------------------------------------------------------- spec conformance
def test_paths_match_openapi_and_responses_validate(api):
    spec = yaml.safe_load((DR / "integrations" / "payroll_api_openapi.yaml").read_text())
    assert set(schemas.BY_PATH) <= set(spec["paths"])
    urls = {
        "/tenants/{tenant_id}": "/tenants/C-1004",
        "/tenants/{tenant_id}/payroll-runs": "/tenants/C-1004/payroll-runs?month=2026-08",
        "/payroll-runs/{run_id}/status": "/payroll-runs/RUN-C-1004-2026-08/status",
        "/payroll-runs/{run_id}/employees/{employee_id}/payslip": "/payroll-runs/RUN-C-1004-2026-08/employees/E-10234/payslip",
        "/statutory/rates": "/statutory/rates?region=UAE&as_of=2026-08-10",
    }
    assert set(urls) == set(schemas.BY_PATH)
    for path, url in urls.items():
        assert "get" in spec["paths"][path]
        r = api.get(url, headers=H("C-1004"))
        assert r.status_code == 200, (url, r.text)
        jsonschema.validate(r.json(), schemas.BY_PATH[path], format_checker=jsonschema.FormatChecker())
        assert not scan(r.text, presidio=True, ner=True), f"PII in {url}: {r.text}"


def test_mutating_endpoints_405(api):
    spec = yaml.safe_load((DR / "integrations" / "payroll_api_openapi.yaml").read_text())
    posts = [p for p, v in spec["paths"].items() if "post" in v]
    assert posts
    for p in posts:
        assert api.post(p.replace("{tenant_id}", "C-1001"), headers=H(), json={}).status_code == 405
    for m in ("put", "patch", "delete"):
        assert getattr(api, m)("/tenants/C-1001", headers=H()).status_code == 405


def test_auth_and_tenant_header(api):
    assert api.get("/tenants/C-1001").status_code == 401
    assert api.get("/tenants/C-1001", headers=H(token="bad")).status_code == 401
    assert api.get("/tenants/C-1001", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 400
    assert api.get("/tenants/C-9999", headers=H("C-9999")).status_code == 403
    assert api.get("/health").json()["status"] == "ok"


# ---------------------------------------------------------------- tenant isolation
def test_api_cross_tenant_forbidden(api):
    assert api.get("/tenants/C-1004", headers=H("C-1001")).status_code == 403
    assert api.get("/tenants/C-1004/payroll-runs", headers=H("C-1001")).status_code == 403
    r = api.get("/payroll-runs/RUN-C-1004-2026-08/status", headers=H("C-1001"))
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    assert api.get("/payroll-runs/RUN-C-1004-2026-08/employees/E-1/payslip", headers=H("C-1001")).status_code == 403
    assert api.get("/statutory/rates?region=UAE", headers=H("C-1001")).status_code == 403  # C-1001 is IN


def test_tool_cross_tenant_c1001_asks_for_c1004(wired):
    """Gate 3: Kestrel (C-1001) ticket asking for Falcon (C-1004) data gets nothing."""
    body, _ = run_tool(T.get_payroll_run_status, {"run_id": "RUN-C-1004-2026-08"}, tenant="C-1001")
    assert body == {"ok": False, "error": "not_found_or_forbidden"}
    body, _ = run_tool(T.get_employee_payslip_status,
                       {"run_id": "RUN-C-1004-2026-08", "employee_id": "E-10234"}, tenant="C-1001")
    assert body["ok"] is False and "Falcon" not in json.dumps(body)
    # tenant profile and run list are locked to the injected tenant, not the model's request
    body, _ = run_tool(T.get_tenant_profile, {}, tenant="C-1001")
    assert body["tenant_id"] == "C-1001"
    body, _ = run_tool(T.list_payroll_runs, {}, tenant="C-1001")
    assert {r["tenant_id"] for r in body["items"]} == {"C-1001"}


def test_model_supplied_tenant_id_is_ignored(wired):
    for t in T.ALL_TOOLS:
        assert "tenant_id" not in t.tool_call_schema.model_json_schema()["properties"], t.name
        assert "received_at" not in t.tool_call_schema.model_json_schema()["properties"], t.name
    # even if a model emits tenant_id in the call, ToolNode injects state's value
    body, _ = run_tool(T.get_tenant_profile, {"tenant_id": "C-1004"}, tenant="C-1001")
    assert body["tenant_id"] == "C-1001"


# ---------------------------------------------------------------- rate limit
def test_31st_call_is_429_with_retry_after(api, clock):
    for i in range(30):
        assert api.get("/payroll-runs/RUN-C-1001-2026-08/status", headers=H()).status_code == 200, i
    r = api.get("/payroll-runs/RUN-C-1001-2026-08/status", headers=H())
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert api.get("/payroll-runs/RUN-C-1002-2026-08/status", headers=H("C-1002")).status_code == 200  # per tenant
    clock.t += 61
    assert api.get("/payroll-runs/RUN-C-1001-2026-08/status", headers=H()).status_code == 200


def test_tool_gives_up_cleanly_on_long_retry_after(wired, app, sleeps):
    for _ in range(30):
        app.state.limiter.check(("C-1001", TOKEN))
    wired.max_wait = 0
    body, _ = run_tool(T.get_payroll_run_status, {"run_id": "RUN-C-1001-2026-08"})
    assert body["ok"] is False and body["error"] == "rate_limited" and body["retry_after"] >= 1 and not sleeps


def test_tool_retries_then_succeeds(wired, app, sleeps, clock):
    wired.max_wait = 100
    for _ in range(30):
        app.state.limiter.check(("C-1001", TOKEN))
    wired.sleep = lambda s: (sleeps.append(s), setattr(clock, "t", clock.t + s))
    body, _ = run_tool(T.get_payroll_run_status, {"run_id": "RUN-C-1001-2026-08"})
    assert body["ok"] is True and len(sleeps) == 1 and sleeps[0] >= 1


# ---------------------------------------------------------------- as_of
def test_api_as_of_uae_wps(api):
    old = api.get("/statutory/rates?region=UAE&as_of=2026-07-01", headers=H("C-1004")).json()
    new = api.get("/statutory/rates?region=UAE&as_of=2026-08-10", headers=H("C-1004")).json()
    wps = lambda d: next(r for r in d["rules"] if r["topic"] == "wps_file_format")
    assert wps(old)["rule_id"] is None and wps(old)["value"].startswith("SIF v2")
    assert wps(new)["rule_id"] == "R-UAE-WPS-2026-07" and wps(new)["value"].startswith("SIF v3")
    # no as_of -> today's (the real API's behaviour; the gotcha)
    assert api.get("/statutory/rates?region=UAE", headers=H("C-1004")).json()["as_of"] == "2026-11-09"


def test_tool_always_sends_as_of_ticket_date(wired):
    seen = []
    orig = wired.http.get
    wired.http.get = lambda *a, **k: (seen.append(k.get("params")), orig(*a, **k))[1]
    body, _ = run_tool(T.get_statutory_rule, {"rule_area": "wps", "region": "UAE"},
                       tenant="C-1004", received_at="2026-07-01T08:00:00Z")
    assert seen[-1]["as_of"] == "2026-07-01"
    assert body["rules"][0]["rule_id"] is None and body["rules"][0]["value"].startswith("SIF v2")
    body, _ = run_tool(T.get_statutory_rule, {"rule_area": "WPS file", "region": "UAE"},
                       tenant="C-1004", received_at="2026-08-10T08:00:00Z")
    assert seen[-1]["as_of"] == "2026-08-10"
    assert body["rules"][0]["rule_id"] == "R-UAE-WPS-2026-07"
    # missing received_at -> refuses instead of defaulting to today
    body, _ = run_tool(T.get_statutory_rule, {"rule_area": "wps", "region": "UAE"}, tenant="C-1004", received_at="")
    assert body["ok"] is False


# ---------------------------------------------------------------- lanes, recording, PII
def test_get_tools_lane_mapping():
    names = lambda lane: {t.name for t in T.get_tools(lane)}
    assert T.get_tools("block") == [] and T.get_tools("human_review") == []
    assert T.get_tools("incident") == [] and T.get_tools(None) == []
    assert names("route_queue") == {"get_payroll_run_status", "list_payroll_runs", "get_employee_payslip_status"}
    full = names("draft_for_agent")
    assert full == names("auto_answer") and {"get_statutory_rule", "search_kb", "get_tenant_profile"} <= full
    assert names("route_queue") < full


def test_records_from_messages_masked(wired):
    _, msgs = run_tool(T.get_employee_payslip_status, {"run_id": "RUN-C-1004-2026-08", "employee_id": "E-10234"},
                       tenant="C-1004")
    rec = T.records_from_messages(msgs)
    assert rec == [{"name": "get_employee_payslip_status",
                    "args_masked": {"run_id": "RUN-C-1004-2026-08", "employee_id": "*****34"}, "ok": True}]
    _, msgs = run_tool(T.get_payroll_run_status, {"run_id": "RUN-C-1004-2026-08"}, tenant="C-1001")
    assert T.records_from_messages(msgs)[0]["ok"] is False


def test_tool_output_pii_is_masked():
    out = T._out({"ok": True, "note": "contact ravi@example.com or IBAN AE070331234567890123456"})
    assert "ravi@example.com" not in out and "AE0703312345" not in out


def test_payslip_has_no_amounts_or_names(api):
    r = api.get("/payroll-runs/RUN-C-1004-2026-08/employees/E-10234/payslip", headers=H("C-1004")).json()
    assert set(r) == {"run_id", "employee_ref", "period", "status", "currency"} and "10234" not in json.dumps(r)


# ---------------------------------------------------------------- KB (needs db)
@pytest.mark.db
def test_search_kb_region_asof_no_superseded(wired, owner):
    body, _ = run_tool(T.search_kb, {"query": "WPS SIF file format"}, tenant="C-1004",
                       received_at="2026-08-10T09:00:00Z")
    assert body["ok"] and body["results"]
    assert all(r["region"] in ("UAE", "ALL") for r in body["results"])
    ids = [r["source_id"] for r in body["results"]]
    assert "R-UAE-WPS-2026-07" in ids
    body, _ = run_tool(T.search_kb, {"query": "PF wage ceiling"}, tenant="C-1004", received_at="2026-08-10T09:00:00Z")
    assert all(r["region"] != "IN" for r in body["results"])


@pytest.mark.db
def test_search_kb_unknown_tenant_empty(wired, owner):
    body, _ = run_tool(T.search_kb, {"query": "WPS"}, tenant="C-9999")
    assert body["ok"] is False
