#!/usr/bin/env python3
"""P2 SQS + EventBridge Scheduler probe. Run: AWS_PROFILE=paynest uv run --python 3.12 --with "boto3[crt]" infra/probes/p2_sqs_scheduler.py [--cleanup]"""
import datetime, json, os, sys, time
from pathlib import Path
import boto3
from botocore.exceptions import ClientError

REGION = "eu-north-1"
PROBE = "p2_sqs_scheduler"
P = "paynest-probe"
RESULT = Path(__file__).parent / "results" / f"{PROBE}.json"
S = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "paynest"), region_name=REGION)
sqs, iam, sch, sts = (S.client(x) for x in ["sqs", "iam", "scheduler", "sts"])
ACCT = sts.get_caller_identity()["Account"]
QN, ROLE, SCHED = f"{P}-q", f"{P}-scheduler-role", f"{P}-once"
res = dict(probe=PROBE, status="FAIL", checks=[], ids_resolved={}, latency_ms={}, est_cost_usd=0.0, error=None,
           fallback=None, notes=[])


def check(name, ok, ev=""):
    res["checks"].append(dict(name=name, **{"pass": bool(ok)}, evidence=str(ev)[:600]))
    print("PASS " if ok else "FAIL ", name, str(ev)[:200], flush=True)


def cleanup():
    print("== cleanup")
    for f, kw in [(sch.delete_schedule, dict(Name=SCHED))]:
        try: f(**kw)
        except ClientError as e: print(" note", e.response["Error"]["Code"])
    try:
        for pn in iam.list_role_policies(RoleName=ROLE)["PolicyNames"]:
            iam.delete_role_policy(RoleName=ROLE, PolicyName=pn)
        iam.delete_role(RoleName=ROLE)
    except ClientError as e: print(" note", e.response["Error"]["Code"])
    try: sqs.delete_queue(QueueUrl=sqs.get_queue_url(QueueName=QN)["QueueUrl"])
    except ClientError as e: print(" note", e.response["Error"]["Code"])


def run():
    ids = res["ids_resolved"]
    t = time.time()
    q = sqs.create_queue(QueueName=QN, tags={"project": "paynest-probe", "probe": PROBE})["QueueUrl"]
    qarn = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    ids["queue_url"] = q
    sqs.send_message(QueueUrl=q, MessageBody="local-test")
    m = sqs.receive_message(QueueUrl=q, WaitTimeSeconds=5).get("Messages", [])
    ok = bool(m) and m[0]["Body"] == "local-test"
    if m: sqs.delete_message(QueueUrl=q, ReceiptHandle=m[0]["ReceiptHandle"])
    check("sqs_send_receive_delete", ok, m[0]["Body"] if m else "no message")
    res["latency_ms"]["sqs_roundtrip"] = int((time.time() - t) * 1000)

    trust = dict(Version="2012-10-17", Statement=[dict(Effect="Allow", Principal=dict(Service="scheduler.amazonaws.com"),
        Action="sts:AssumeRole", Condition=dict(StringEquals={"aws:SourceAccount": ACCT}))])
    role = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                           Tags=[{"Key": "project", "Value": "paynest-probe"}, {"Key": "probe", "Value": PROBE}])["Role"]["Arn"]
    iam.put_role_policy(RoleName=ROLE, PolicyName="send", PolicyDocument=json.dumps(dict(Version="2012-10-17",
        Statement=[dict(Effect="Allow", Action="sqs:SendMessage", Resource=qarn)])))
    ids["scheduler_role"] = role
    time.sleep(12)
    when = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    sa = sch.create_schedule(Name=SCHED, ScheduleExpression=f"at({when})", ScheduleExpressionTimezone="UTC",
        FlexibleTimeWindow=dict(Mode="OFF"), ActionAfterCompletion="DELETE",
        Target=dict(Arn=qarn, RoleArn=role, Input="scheduled-hello"))["ScheduleArn"]
    ids["schedule_arn"] = sa
    g = sch.get_schedule(Name=SCHED)
    check("schedule_created", g["ActionAfterCompletion"] == "DELETE", f"{when} UTC {sa} ActionAfterCompletion={g['ActionAfterCompletion']}")
    t = time.time()
    got = None
    while time.time() - t < 300 and not got:
        r = sqs.receive_message(QueueUrl=q, WaitTimeSeconds=10).get("Messages", [])
        if r:
            got = r[0]; sqs.delete_message(QueueUrl=q, ReceiptHandle=got["ReceiptHandle"])
    res["latency_ms"]["scheduled_message_wait"] = int((time.time() - t) * 1000)
    check("scheduled_message_arrived", bool(got), got["Body"] if got else "timeout 300s")
    time.sleep(20)
    try:
        sch.get_schedule(Name=SCHED); gone = False
    except ClientError as e:
        gone = e.response["Error"]["Code"] == "ResourceNotFoundException"
    check("schedule_auto_deleted_after_completion", gone, "ResourceNotFound" if gone else "still exists")
    # ECS RunTask target: record only (no dry run exists); validate role trust/permissions via IAM simulate
    try:
        sim = iam.simulate_principal_policy(PolicySourceArn=role, ActionNames=["ecs:RunTask"],
                                            ResourceArns=[f"arn:aws:ecs:{REGION}:{ACCT}:task-definition/x:1"])
        res["notes"].append("ECS RunTask target not exercised (no dry run). scheduler.amazonaws.com CreateSchedule supports "
                            "ecs target type (Arn=cluster, EcsParameters); role would need ecs:RunTask + iam:PassRole. "
                            f"Simulate for current probe role (only has sqs:SendMessage): {sim['EvaluationResults'][0]['EvalDecision']}")
    except ClientError as e:
        res["notes"].append(f"ECS RunTask target check skipped: {e}")


def main():
    if "--cleanup" in sys.argv:
        cleanup(); return
    try:
        run()
    except ClientError as e:
        res["error"] = str(e); check("aws_error", False, str(e))
        res["_d"] = any(k in str(e) for k in ("AccessDenied", "explicit deny", "UnauthorizedOperation"))
    finally:
        cleanup()
        res["est_cost_usd"] = 0.0
        d = res.pop("_d", False)
        res["status"] = "PASS" if res["checks"] and all(c["pass"] for c in res["checks"]) else ("BLOCKED" if d else "FAIL")
        if res["status"] == "BLOCKED":
            res["fallback"] = "Use SQS delay queues / Lambda or ECS-side timers instead of EventBridge Scheduler."
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(res, indent=2, default=str))
        print("STATUS", res["status"])


if __name__ == "__main__":
    main()
