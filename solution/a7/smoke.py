"""In-VPC smoke client for the internal ALB (a laptop cannot reach it). Runs as the `smoke` RunTask definition.

    python -m solution.a7.smoke health
    python -m solution.a7.smoke queue --tenant C-1004
    python -m solution.a7.smoke ticket --tenant C-1004 --ticket PN-100402
    python -m solution.a7.smoke decide --tenant C-1004 --ticket PN-100402 --status approved --reviewer demo
    python -m solution.a7.smoke flags                     (GET /admin/flags)
    python -m solution.a7.smoke payroll --tenant C-1004   (mock payroll via ALB:8081)

Env: A7_ALB_URL (http://<alb>), A7_PAYROLL_API_URL, A7_REVIEWER_TOKEN, A7_ADMIN_TOKEN (ECS secrets). Prints the HTTP status and the
JSON body; bodies hold masked text only. Tokens are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def call(method: str, url: str, headers: dict[str, str], body: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, method=method, headers={"Content-Type": "application/json", **headers},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["health", "queue", "ticket", "decide", "flags", "payroll"])
    ap.add_argument("--tenant")
    ap.add_argument("--ticket")
    ap.add_argument("--status", default="approved")
    ap.add_argument("--reviewer", default="demo-reviewer")
    a = ap.parse_args(argv)
    base = os.environ.get("A7_ALB_URL", "").rstrip("/")
    rev = {"Authorization": f"Bearer {os.environ.get('A7_REVIEWER_TOKEN', '')}", "X-Tenant-Id": a.tenant or ""}
    if a.cmd == "health":
        code, text = call("GET", f"{base}/health", {})
    elif a.cmd == "queue":
        code, text = call("GET", f"{base}/shadow", rev)
    elif a.cmd == "ticket":
        code, text = call("GET", f"{base}/tickets/{a.ticket}", rev)
    elif a.cmd == "decide":
        code, text = call("POST", f"{base}/tickets/{a.ticket}/decision", rev, {"status": a.status, "reviewer": a.reviewer})
    elif a.cmd == "flags":
        code, text = call("GET", f"{base}/admin/flags", {"Authorization": f"Bearer {os.environ.get('A7_ADMIN_TOKEN', '')}"})
    else:
        code, text = call("GET", f"{os.environ.get('A7_PAYROLL_API_URL', '').rstrip('/')}/tenants/{a.tenant}",
                          {"Authorization": f"Bearer {os.environ.get('A7_PAYROLL_API_TOKEN', '')}", "X-Tenant-ID": a.tenant or ""})
    print(code, text, flush=True)
    return 0 if 200 <= code < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
