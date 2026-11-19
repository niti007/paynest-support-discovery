"""Phase 6 app-side AWS hooks (no AWS, no DB): composed DSNs, Scheduler hook, SQS message dispatch."""
import datetime as dt
import json

import pytest

from solution.a7 import db, escalation, human_db, sqs_worker

UTC = dt.timezone.utc


def test_local_dsn_defaults_unchanged(monkeypatch):
    for v in ("A7_DB_HOST", "A7_DATABASE_URL", "A7_APP_DATABASE_URL"):
        monkeypatch.delenv(v, raising=False)
    assert db.dsn() == db.DEFAULT_DSN and db.app_dsn() == db.DEFAULT_APP_DSN


def test_composed_dsn_from_parts(monkeypatch):
    monkeypatch.delenv("A7_DATABASE_URL", raising=False)
    monkeypatch.delenv("A7_APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("A7_DB_HOST", "db.example.internal")
    monkeypatch.setenv("A7_APP_USER_PASSWORD", "p@ss/w:rd")
    monkeypatch.setenv("A7_DB_OWNER_PASSWORD", "own")
    assert db.app_dsn() == "postgresql://app_user:p%40ss%2Fw%3Ard@db.example.internal:5432/a7?sslmode=require"
    assert db.dsn().startswith("postgresql://pnadmin:own@db.example.internal:5432/a7")
    monkeypatch.delenv("A7_ACT_DATABASE_URL", raising=False)
    monkeypatch.setenv("A7_ACT_PASSWORD", "act")
    assert human_db.act_dsn() == "postgresql://a7_act:act@db.example.internal:5432/a7?sslmode=require"   # act task: only host + act password


class FakeScheduler:
    def __init__(self, exc=None):
        self.calls, self.exc = [], exc

    def create_schedule(self, **kw):
        if self.exc:
            raise self.exc
        self.calls.append(kw)


def _env(monkeypatch):
    monkeypatch.setenv("A7_SCHEDULER_GROUP", "g")
    monkeypatch.setenv("A7_ESCALATION_QUEUE_ARN", "arn:aws:sqs:eu-north-1:000000000000:q")
    monkeypatch.setenv("A7_SCHEDULER_ROLE_ARN", "arn:aws:iam::000000000000:role/r")


def test_scheduler_disabled_by_default(monkeypatch):
    monkeypatch.delenv("A7_ESCALATION_BACKEND", raising=False)
    assert not escalation.scheduler_enabled()
    monkeypatch.setenv("A7_ESCALATION_BACKEND", "scheduler")
    assert escalation.scheduler_enabled()


def test_schedule_future_and_overdue(monkeypatch):
    _env(monkeypatch)
    now = dt.datetime(2026, 9, 10, 8, 0, tzinfo=UTC)
    c = FakeScheduler()
    assert escalation.schedule_escalation(7, now + dt.timedelta(hours=1), client=c, now=now)
    assert escalation.schedule_escalation(8, now - dt.timedelta(hours=5), client=c, now=now)
    a, b = c.calls
    assert a["Name"] == "a7-esc-7" and a["ScheduleExpression"] == "at(2026-09-10T09:00:00)"
    assert json.loads(a["Target"]["Input"]) == {"escalation_id": 7} and a["ActionAfterCompletion"] == "DELETE"
    assert b["ScheduleExpression"] == "at(2026-09-10T08:01:00)"          # overdue: fires in one minute


def test_schedule_never_raises(monkeypatch):
    _env(monkeypatch)
    assert escalation.schedule_escalation(1, dt.datetime.now(UTC), client=FakeScheduler(RuntimeError("boom"))) is False
    Conflict = type("ConflictException", (Exception,), {})
    assert escalation.schedule_escalation(1, dt.datetime.now(UTC), client=FakeScheduler(Conflict())) is False


def test_dispatch(monkeypatch):
    monkeypatch.setattr(escalation, "handler", lambda ev, ctx=None: {"fired": [ev.get("escalation_id")]} if ev else {"fired": []})
    assert sqs_worker.handle_body({"escalation_id": 5}, None, None) == {"kind": "escalation", "fired": [5]}
    assert sqs_worker.handle_body({"run_due": True}, None, None) == {"kind": "run_due", "fired": []}
    seen = []

    def run(rec):
        seen.append(rec)
        return {"ticket_id": "T1", "lane": "route_queue", "category": "payroll", "priority": "P2", "draft": "SECRET-BODY"}

    out = sqs_worker.handle_body({"ticket_id": "T1"}, run, lambda t: {"ticket_id": t})
    assert seen == [{"ticket_id": "T1"}]
    assert out == {"kind": "ticket", "ticket": "T1", "lane": "route_queue", "category": "payroll", "priority": "P2", "paused": None}
    assert "SECRET-BODY" not in json.dumps(out)
    with pytest.raises(ValueError):
        sqs_worker.handle_body({"nope": 1}, run, lambda t: {})


def test_heartbeat_extends_visibility_while_running_and_stops():
    import time

    class FakeSqs:
        def __init__(self):
            self.calls = []

        def change_message_visibility(self, **kw):
            self.calls.append(kw)

    f = FakeSqs()
    with sqs_worker.Heartbeat(f, "q", "rh", every=0.05, timeout=300):
        time.sleep(0.3)
    n = len(f.calls)
    assert n >= 2 and all(c == {"QueueUrl": "q", "ReceiptHandle": "rh", "VisibilityTimeout": 300} for c in f.calls)
    time.sleep(0.15)
    assert len(f.calls) == n            # stopped with the context
