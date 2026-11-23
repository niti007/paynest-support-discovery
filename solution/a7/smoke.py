"""In-VPC smoke client for the internal ALB (a laptop cannot reach it). Runs as the `smoke` RunTask definition.

    python -m solution.a7.smoke health
    python -m solution.a7.smoke queue --tenant C-1004
    python -m solution.a7.smoke ticket --tenant C-1004 --ticket PN-100402   (any processed ticket: lane, queue, priority)
    python -m solution.a7.smoke batch --tickets PN-101230,PN-101231 PN-101235..PN-101239   (replay ids through the ingest queue)
    python -m solution.a7.smoke decide --tenant C-1004 --ticket PN-100402 --status approved --reviewer demo
    python -m solution.a7.smoke flags                     (GET /admin/flags)
    python -m solution.a7.smoke payroll --tenant C-1004   (mock payroll via ALB:8081)

Env: A7_QUEUE_URL + AWS_REGION (batch only; the smoke task role may SendMessage to that queue and nothing else), A7_ALB_URL (http://<alb>), A7_PAYROLL_API_URL, A7_REVIEWER_TOKEN, A7_ADMIN_TOKEN (ECS secrets). Prints the HTTP status and the
JSON body; bodies hold masked text only. Tokens are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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


TICKET_RE = re.compile(r"^PN-(\d{1,9})$")
MAX_BATCH = 500


def parse_tickets(items: list[str]) -> list[str]:
    """Ids separated by commas or spaces; `PN-101235..PN-101239` is an inclusive range. Order kept, duplicates dropped."""
    out: list[str] = []
    for tok in (t for item in items for t in item.split(",") if t.strip()):
        tok = tok.strip()
        if ".." in tok:
            a, b = (TICKET_RE.match(x) for x in tok.split("..", 1))
            if not (a and b) or int(b.group(1)) < int(a.group(1)):
                raise ValueError(f"bad ticket range {tok!r}")
            width = len(a.group(1))
            ids = [f"PN-{n:0{width}d}" for n in range(int(a.group(1)), int(b.group(1)) + 1)]
        elif TICKET_RE.match(tok):
            ids = [tok]
        else:
            raise ValueError(f"bad ticket id {tok!r}")
        out += [i for i in ids if i not in out]
    if not out:
        raise ValueError("no tickets given")
    if len(out) > MAX_BATCH:
        raise ValueError(f"at most {MAX_BATCH} tickets per batch (got {len(out)})")
    return out


def send_batch(ids: list[str], sqs=None, queue_url: str | None = None) -> dict:
    """Send {"ticket_id": id} for each id to the ingest queue (10 per SendMessageBatch). Returns counts and failed ids only."""
    queue_url = queue_url or os.environ.get("A7_QUEUE_URL", "")
    if not queue_url:
        raise ValueError("A7_QUEUE_URL is not set")
    if sqs is None:
        import boto3
        sqs = boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "eu-north-1"))
    sent, failed = 0, []
    for i in range(0, len(ids), 10):
        chunk = ids[i:i + 10]
        resp = sqs.send_message_batch(QueueUrl=queue_url, Entries=[
            {"Id": str(n), "MessageBody": json.dumps({"ticket_id": t})} for n, t in enumerate(chunk)])
        sent += len(resp.get("Successful", []))
        failed += [chunk[int(f["Id"])] for f in resp.get("Failed", [])]
    return {"requested": len(ids), "sent": sent, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["live", "health", "queue", "ticket", "decide", "flags", "payroll", "batch"])
    ap.add_argument("--tenant")
    ap.add_argument("--ticket")
    ap.add_argument("--tickets", nargs="+", help="batch: ids, comma or space separated; A..B is an inclusive range")
    ap.add_argument("--status", default="approved")
    ap.add_argument("--reviewer", default="demo-reviewer")
    a = ap.parse_args(argv)
    if a.cmd == "batch":
        try:
            res = send_batch(parse_tickets(a.tickets or []))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(json.dumps(res), flush=True)
        return 0 if not res["failed"] else 1
    base = os.environ.get("A7_ALB_URL", "").rstrip("/")
    rev = {"Authorization": f"Bearer {os.environ.get('A7_REVIEWER_TOKEN', '')}", "X-Tenant-Id": a.tenant or ""}
    if a.cmd == "live":
        code, text = call("GET", f"{base}/livez", {})
    elif a.cmd == "health":              # readiness: the API can reach the database (503 until the bootstrap task has run)
        code, text = call("GET", f"{base}/readyz", {})
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
