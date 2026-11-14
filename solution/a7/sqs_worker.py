"""AWS worker: long-poll the ingest SQS queue, run pii_gate + normalise + graph per message.

    python -m solution.a7.sqs_worker            (A7_QUEUE_URL, A7_DB_HOST + A7_APP_USER_PASSWORD, A7_KMS_KEY_ID, ...)

Message bodies (JSON):
  {"ticket_id": "PN-100402"}     look the record up in the baked-in data-room (demo replay)
  {"record": {...}}              a raw record (any of the 5 source schemas); raw text lives only inside pii_gate
  {"escalation_id": 12}          fired by an EventBridge Scheduler one-time schedule -> escalation.fire_escalation
  {"run_due": true}              recurring backstop -> escalation.run_due
Success deletes the message; an exception leaves it for the visibility timeout, then the DLQ after 5 receives.
Only masked fields are logged (ticket id, lane, category, priority). Idle exit: A7_IDLE_EXIT_S (0 = run forever).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time
from typing import Any, Callable

log = logging.getLogger("a7.sqs_worker")
LOG_FIELDS = ("ticket", "lane", "category", "priority", "paused")


def handle_body(body: dict[str, Any], run_record: Callable[[dict], dict], find: Callable[[str], dict]) -> dict:
    """Pure dispatch (unit-tested with fakes). Returns a log-safe summary."""
    from . import escalation
    if "escalation_id" in body:
        return {"kind": "escalation", **escalation.handler({"escalation_id": body["escalation_id"]})}
    if body.get("run_due"):
        return {"kind": "run_due", **escalation.handler({})}
    if "record" in body:
        rec = body["record"]
    elif "ticket_id" in body:
        rec = find(str(body["ticket_id"]))
    else:
        raise ValueError("message needs ticket_id, record, escalation_id or run_due")
    from .worker import summary
    return {"kind": "ticket", **{k: v for k, v in summary(run_record(rec)).items() if k in LOG_FIELDS}}


def main() -> None:
    import boto3

    from . import db
    from .graph import build_graph, postgres_checkpointer
    from .nodes.normalise import make_normalise
    from .nodes.pii_gate import make_pii_gate
    from .vault import KmsKeyProvider, LocalKeyProvider, PostgresVaultStore, Vault
    from .worker import find_record, run_ticket

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    region = os.environ.get("AWS_REGION", "eu-north-1")
    queue_url = os.environ["A7_QUEUE_URL"]
    idle_exit = int(os.environ.get("A7_IDLE_EXIT_S", "0"))
    sqs = boto3.client("sqs", region_name=region)
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))

    keys = KmsKeyProvider() if os.environ.get("A7_KMS_KEY_ID") else LocalKeyProvider()   # worker role: GenerateDataKey only
    last_work = time.monotonic()
    with db.connect(app=True, autocommit=True) as vconn, postgres_checkpointer() as cp:
        gate = make_pii_gate(Vault(PostgresVaultStore(vconn), keys))
        graph, norm = build_graph(cp), make_normalise()

        def run(rec: dict) -> dict:
            return run_ticket(rec, graph, gate, norm)

        while not stop["now"]:
            resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20, VisibilityTimeout=300)
            msgs = resp.get("Messages", [])
            if not msgs:
                if idle_exit and time.monotonic() - last_work > idle_exit:
                    log.info("idle for %ss, exiting", idle_exit)
                    return
                continue
            m = msgs[0]
            try:
                out = handle_body(json.loads(m["Body"]), run, find_record)
                log.info("done %s", json.dumps(out, ensure_ascii=False, default=str))
            except Exception as e:  # noqa: BLE001 - message stays on the queue; DLQ after maxReceiveCount
                log.error("message failed: %s", type(e).__name__)
                continue
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
            last_work = time.monotonic()


if __name__ == "__main__":
    main()
