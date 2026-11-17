"""solution.a7.smoke: ticket-id parsing, `smoke batch` (fake SQS client, no AWS) and `smoke ticket`."""
import json

import pytest

from solution.a7 import smoke


def test_parse_tickets_lists_ranges_and_dedupe():
    assert smoke.parse_tickets(["PN-101230,PN-101231", "PN-101231", "PN-101235..PN-101237"]) == [
        "PN-101230", "PN-101231", "PN-101235", "PN-101236", "PN-101237"]


@pytest.mark.parametrize("bad", [["x"], ["PN-1; rm"], ["PN-5..PN-3"], [""], ["PN-1..x"], [f"PN-100000..PN-{100000 + smoke.MAX_BATCH}"]])
def test_parse_tickets_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        smoke.parse_tickets(bad)


class FakeSqs:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def send_message_batch(self, QueueUrl, Entries):
        self.calls.append((QueueUrl, Entries))
        tid = lambda e: json.loads(e["MessageBody"])["ticket_id"]  # noqa: E731
        return {"Successful": [{"Id": e["Id"]} for e in Entries if tid(e) not in self.fail],
                "Failed": [{"Id": e["Id"]} for e in Entries if tid(e) in self.fail]}


def test_send_batch_chunks_by_ten_and_sends_ticket_id_bodies():
    ids = [f"PN-{n}" for n in range(101200, 101225)]
    q = FakeSqs(fail={"PN-101203"})
    res = smoke.send_batch(ids, sqs=q, queue_url="https://sqs.example/q")
    assert [len(c[1]) for c in q.calls] == [10, 10, 5] and {c[0] for c in q.calls} == {"https://sqs.example/q"}
    assert res == {"requested": 25, "sent": 24, "failed": ["PN-101203"]}
    assert json.loads(q.calls[0][1][0]["MessageBody"]) == {"ticket_id": "PN-101200"}


def test_send_batch_needs_a_queue_url(monkeypatch):
    monkeypatch.delenv("A7_QUEUE_URL", raising=False)
    with pytest.raises(ValueError):
        smoke.send_batch(["PN-1"], sqs=FakeSqs())


def test_main_batch_prints_counts_only(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "send_batch", lambda ids, sqs=None, queue_url=None: {"requested": len(ids), "sent": len(ids), "failed": []})
    assert smoke.main(["batch", "--tickets", "PN-101238..PN-101239"]) == 0
    assert json.loads(capsys.readouterr().out) == {"requested": 2, "sent": 2, "failed": []}


def test_main_batch_bad_input_exits_2_and_failed_exits_1(monkeypatch):
    assert smoke.main(["batch", "--tickets", "nope"]) == 2
    monkeypatch.setattr(smoke, "send_batch", lambda ids, sqs=None, queue_url=None: {"requested": 1, "sent": 0, "failed": ids})
    assert smoke.main(["batch", "--tickets", "PN-1"]) == 1
    assert smoke.main(["batch"]) == 2


def test_main_ticket_prints_status_and_body(monkeypatch, capsys):
    seen = {}

    def fake_call(method, url, headers, body=None):
        seen.update(method=method, url=url, tenant=headers["X-Tenant-Id"])
        return 200, json.dumps({"lane": "route_queue", "queue": "Payroll-Ops", "priority": "P2"})
    monkeypatch.setattr(smoke, "call", fake_call)
    monkeypatch.setenv("A7_ALB_URL", "http://alb")
    assert smoke.main(["ticket", "--tenant", "C-1004", "--ticket", "PN-101055"]) == 0
    assert seen == {"method": "GET", "url": "http://alb/tickets/PN-101055", "tenant": "C-1004"}
    assert '"queue": "Payroll-Ops"' in capsys.readouterr().out
