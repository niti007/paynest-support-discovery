#!/usr/bin/env python3
"""P2 network+compute probe: private VPC, endpoints, ECR, ECS Fargate (arm64), internal ALB.
Run: AWS_PROFILE=paynest uv run --python 3.12 --with "boto3[crt]" infra/probes/p2_network_compute.py [--cleanup]
Region eu-north-1 only. Everything is torn down in a finally block (cleanup() is discovery-based).
"""
import base64, json, os, subprocess, sys, tempfile, time
from pathlib import Path
import boto3
from botocore.exceptions import ClientError, WaiterError

REGION = "eu-north-1"
PROBE = "p2_network_compute"
P = "paynest-probe"
TAGS = [{"Key": "project", "Value": "paynest-probe"}, {"Key": "probe", "Value": PROBE}]
CIDR = "10.42.0.0/16"
MODEL_ID = os.environ.get("PROBE_MODEL_ID", "amazon.nova-lite-v1:0")  # eu.anthropic.claude-haiku-5-5 not available to account
RESULT = Path(__file__).parent / "results" / f"{PROBE}.json"
S = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "paynest"), region_name=REGION)
ec2, ecs, ecr, elb, iam, logs, sqs, sts = (S.client(x) for x in
    ["ec2", "ecs", "ecr", "elbv2", "iam", "logs", "sqs", "sts"])
ACCT = sts.get_caller_identity()["Account"]
NAMES = dict(vpc=f"{P}-vpc", repo=f"{P}-hello", cluster=f"{P}-cluster", alb=f"{P}-alb", tg=f"{P}-tg",
             queue=f"{P}-q-net", lg="/paynest-probe/ecs", exec_role=f"{P}-exec-role", task_role=f"{P}-task-role",
             sg=f"{P}-sg")
res = dict(probe=PROBE, status="FAIL", checks=[], ids_resolved={}, latency_ms={}, est_cost_usd=0.0, error=None,
           fallback=None, notes=[])


def check(name, ok, evidence=""):
    res["checks"].append(dict(name=name, pass_=bool(ok), evidence=str(evidence)[:600]))
    print(("PASS " if ok else "FAIL ") + name, str(evidence)[:200], flush=True)


def timed(key):
    class T:
        def __enter__(s): s.t = time.time()
        def __exit__(s, *a): res["latency_ms"][key] = int((time.time() - s.t) * 1000)
    return T()


def tag_spec(rt):
    return [dict(ResourceType=rt, Tags=TAGS + [{"Key": "Name", "Value": NAMES["vpc"]}])]


def wait_until(fn, timeout, interval=5, desc=""):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(interval)
    return None


# ---------------------------------------------------------------- cleanup (discovery-based)
def cleanup():
    print("== cleanup", flush=True)
    def safe(f, *a, **k):
        try:
            return f(*a, **k)
        except ClientError as e:
            print("  cleanup note:", e.response["Error"]["Code"], str(e)[:120])
        except Exception as e:
            print("  cleanup note:", str(e)[:120])
    # ECS
    cl = safe(ecs.describe_clusters, clusters=[NAMES["cluster"]])
    if cl and any(c["status"] == "ACTIVE" for c in cl["clusters"]):
        arns = ecs.list_tasks(cluster=NAMES["cluster"])["taskArns"]
        for a in arns:
            safe(ecs.stop_task, cluster=NAMES["cluster"], task=a, reason="probe cleanup")
        if arns:
            wait_until(lambda: not ecs.list_tasks(cluster=NAMES["cluster"])["taskArns"], 180, 5)
        safe(ecs.delete_cluster, cluster=NAMES["cluster"])
    fams = safe(ecs.list_task_definitions, familyPrefix=P, status="ACTIVE")
    for a in (fams or {}).get("taskDefinitionArns", []):
        safe(ecs.deregister_task_definition, taskDefinition=a)
    # ALB
    lbs = []
    try:
        lbs = elb.describe_load_balancers(Names=[NAMES["alb"]])["LoadBalancers"]
    except ClientError:
        pass
    for lb in lbs:
        for l in elb.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"])["Listeners"]:
            safe(elb.delete_listener, ListenerArn=l["ListenerArn"])
        safe(elb.delete_load_balancer, LoadBalancerArn=lb["LoadBalancerArn"])
        wait_until(lambda: not _lb_exists(lb["LoadBalancerArn"]), 240, 5)
    try:
        for tg in elb.describe_target_groups(Names=[NAMES["tg"]])["TargetGroups"]:
            for _ in range(12):
                try:
                    elb.delete_target_group(TargetGroupArn=tg["TargetGroupArn"]); break
                except ClientError:
                    time.sleep(5)
    except ClientError:
        pass
    # VPC
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:project", "Values": ["paynest-probe"]},
                                      {"Name": "tag:probe", "Values": [PROBE]}])["Vpcs"]
    for v in vpcs:
        vid = v["VpcId"]
        eps = ec2.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [vid]}])["VpcEndpoints"]
        eps = [e for e in eps if e["State"] not in ("deleted", "deleting")] or []
        if eps:
            safe(ec2.delete_vpc_endpoints, VpcEndpointIds=[e["VpcEndpointId"] for e in eps])
        wait_until(lambda: not [e for e in ec2.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [vid]}])
                                ["VpcEndpoints"] if e["State"] != "deleted"], 300, 5)
        wait_until(lambda: not ec2.describe_network_interfaces(Filters=[{"Name": "vpc-id", "Values": [vid]}])
                   ["NetworkInterfaces"], 600, 10)
        for sg in ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vid]}])["SecurityGroups"]:
            if sg["GroupName"] != "default":
                safe(ec2.delete_security_group, GroupId=sg["GroupId"])
        for sn in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vid]}])["Subnets"]:
            safe(ec2.delete_subnet, SubnetId=sn["SubnetId"])
        for rt in ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vid]}])["RouteTables"]:
            if not any(a.get("Main") for a in rt["Associations"]):
                for a in rt["Associations"]:
                    safe(ec2.disassociate_route_table, AssociationId=a["RouteTableAssociationId"])
                safe(ec2.delete_route_table, RouteTableId=rt["RouteTableId"])
        safe(ec2.delete_vpc, VpcId=vid)
    # ECR
    safe(ecr.delete_repository, repositoryName=NAMES["repo"], force=True)
    # IAM
    for r in (NAMES["exec_role"], NAMES["task_role"]):
        try:
            for pn in iam.list_role_policies(RoleName=r)["PolicyNames"]:
                iam.delete_role_policy(RoleName=r, PolicyName=pn)
            for ap in iam.list_attached_role_policies(RoleName=r)["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=r, PolicyArn=ap["PolicyArn"])
            iam.delete_role(RoleName=r)
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                print("  iam note", str(e)[:120])
    safe(logs.delete_log_group, logGroupName=NAMES["lg"])
    try:
        sqs.delete_queue(QueueUrl=sqs.get_queue_url(QueueName=NAMES["queue"])["QueueUrl"])
    except ClientError:
        pass


def _lb_exists(arn):
    try:
        return bool(elb.describe_load_balancers(LoadBalancerArns=[arn])["LoadBalancers"])
    except ClientError:
        return False


# ---------------------------------------------------------------- app image
APP = '''import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
import boto3
from botocore.config import Config
R = os.environ.get("AWS_REGION", "eu-north-1")
cfg = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2})
def work():
    try:
        b = boto3.client("bedrock-runtime", region_name=R, config=cfg)
        r = b.converse(modelId=os.environ["MODEL_ID"], messages=[{"role": "user", "content": [{"text": "Say ok"}]}],
                       inferenceConfig={"maxTokens": 20})
        print("BEDROCK_OK", r["output"]["message"]["content"][0]["text"][:40], flush=True)
    except Exception as e:
        print("BEDROCK_FAIL", str(e)[:300], flush=True)
    try:
        q = boto3.client("sqs", region_name=R, config=cfg)
        q.send_message(QueueUrl=os.environ["QUEUE_URL"], MessageBody="hello from fargate")
        print("SQS_OK", flush=True)
    except Exception as e:
        print("SQS_FAIL", str(e)[:300], flush=True)
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/health"
        self.send_response(200 if ok else 404); self.end_headers(); self.wfile.write(b"ok" if ok else b"")
    def log_message(self, *a): pass
threading.Thread(target=work, daemon=True).start()
HTTPServer(("0.0.0.0", 8080), H).serve_forever()
'''
DOCKERFILE = '''FROM --platform=linux/arm64 python:3.12-slim
RUN pip install --no-cache-dir boto3
COPY app.py /app.py
CMD ["python", "-u", "/app.py"]
'''


def build_push(repo_uri):
    d = tempfile.mkdtemp(prefix="p2build-")
    Path(d, "app.py").write_text(APP)
    Path(d, "Dockerfile").write_text(DOCKERFILE)
    tok = ecr.get_authorization_token()["authorizationData"][0]
    user, pw = base64.b64decode(tok["authorizationToken"]).decode().split(":", 1)
    host = repo_uri.split("/")[0]
    subprocess.run(["docker", "login", "--username", user, "--password-stdin", host], input=pw.encode(),
                   check=True, capture_output=True)
    tag = f"{repo_uri}:latest"
    subprocess.run(["docker", "build", "--platform", "linux/arm64", "-t", tag, d], check=True, capture_output=True)
    out = subprocess.run(["docker", "push", tag], check=True, capture_output=True, text=True).stdout
    subprocess.run(["docker", "rmi", tag], capture_output=True)
    return tag


# ---------------------------------------------------------------- main probe
def run():
    t0 = time.time()
    ids = res["ids_resolved"]
    # SQS
    q = sqs.create_queue(QueueName=NAMES["queue"], tags={"project": "paynest-probe", "probe": PROBE})["QueueUrl"]
    qarn = sqs.get_queue_attributes(QueueUrl=q, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    ids["queue_url"] = q
    # log group
    logs.create_log_group(logGroupName=NAMES["lg"], tags={"project": "paynest-probe", "probe": PROBE})
    logs.put_retention_policy(logGroupName=NAMES["lg"], retentionInDays=1)
    lg_arn = f"arn:aws:logs:{REGION}:{ACCT}:log-group:{NAMES['lg']}:*"
    # IAM
    trust = json.dumps(dict(Version="2012-10-17", Statement=[dict(Effect="Allow",
        Principal=dict(Service="ecs-tasks.amazonaws.com"), Action="sts:AssumeRole",
        Condition=dict(StringEquals={"aws:SourceAccount": ACCT},
                       ArnLike={"aws:SourceArn": f"arn:aws:ecs:{REGION}:{ACCT}:*"}))]))
    repo_arn = f"arn:aws:ecr:{REGION}:{ACCT}:repository/{NAMES['repo']}"
    ex = iam.create_role(RoleName=NAMES["exec_role"], AssumeRolePolicyDocument=trust, Tags=TAGS)["Role"]["Arn"]
    iam.put_role_policy(RoleName=NAMES["exec_role"], PolicyName="exec", PolicyDocument=json.dumps(dict(
        Version="2012-10-17", Statement=[
            dict(Effect="Allow", Action="ecr:GetAuthorizationToken", Resource="*"),
            dict(Effect="Allow", Action=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], Resource=repo_arn),
            dict(Effect="Allow", Action=["logs:CreateLogStream", "logs:PutLogEvents"], Resource=lg_arn)])))
    tk = iam.create_role(RoleName=NAMES["task_role"], AssumeRolePolicyDocument=trust, Tags=TAGS)["Role"]["Arn"]
    iam.put_role_policy(RoleName=NAMES["task_role"], PolicyName="task", PolicyDocument=json.dumps(dict(
        Version="2012-10-17", Statement=[
            dict(Effect="Allow", Action=["bedrock:InvokeModel", "bedrock:Converse"], Resource=[
                f"arn:aws:bedrock:{REGION}::foundation-model/{MODEL_ID}",
                f"arn:aws:bedrock:{REGION}:{ACCT}:inference-profile/{MODEL_ID}",
                f"arn:aws:bedrock:*::foundation-model/{MODEL_ID.split('.', 1)[-1]}"]),
            dict(Effect="Allow", Action="sqs:SendMessage", Resource=qarn)])))
    ids.update(exec_role=ex, task_role=tk)
    check("iam_roles_created", True, f"{ex} {tk}")

    # VPC
    with timed("vpc"):
        vpc = ec2.create_vpc(CidrBlock=CIDR, TagSpecifications=tag_spec("vpc"))["Vpc"]["VpcId"]
        ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsSupport={"Value": True})
        ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsHostnames={"Value": True})
        azs = [a["ZoneName"] for a in ec2.describe_availability_zones()["AvailabilityZones"]][:2]
        subs = [ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.42.{i}.0/24", AvailabilityZone=az,
                                  TagSpecifications=tag_spec("subnet"))["Subnet"]["SubnetId"]
                for i, az in enumerate(azs)]
        sg = ec2.create_security_group(GroupName=NAMES["sg"], Description="paynest probe", VpcId=vpc,
                                       TagSpecifications=tag_spec("security-group"))["GroupId"]
        ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[
            dict(IpProtocol="tcp", FromPort=443, ToPort=443, IpRanges=[dict(CidrIp=CIDR)]),
            dict(IpProtocol="tcp", FromPort=8080, ToPort=8080, IpRanges=[dict(CidrIp=CIDR)]),
            dict(IpProtocol="tcp", FromPort=80, ToPort=80, IpRanges=[dict(CidrIp=CIDR)])])
        rt = [r for r in ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["RouteTables"]][0]["RouteTableId"]
        igws = ec2.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc]}])["InternetGateways"]
        nats = ec2.describe_nat_gateways(Filter=[{"Name": "vpc-id", "Values": [vpc]}])["NatGateways"]
    ids.update(vpc=vpc, subnets=subs, azs=azs, sg=sg, route_table=rt)
    check("vpc_private_no_igw_no_nat", not igws and not nats, f"{vpc} subnets={subs} azs={azs} igw={len(igws)} nat={len(nats)}")

    # Endpoints
    with timed("endpoints"):
        s3 = ec2.create_vpc_endpoint(VpcId=vpc, ServiceName=f"com.amazonaws.{REGION}.s3", VpcEndpointType="Gateway",
                                     RouteTableIds=[rt], TagSpecifications=tag_spec("vpc-endpoint"))["VpcEndpoint"]["VpcEndpointId"]
        eps = {"s3": s3}
        for svc in ["bedrock-runtime", "sqs", "kms", "secretsmanager", "ecr.api", "ecr.dkr", "logs"]:
            try:
                eps[svc] = ec2.create_vpc_endpoint(VpcId=vpc, ServiceName=f"com.amazonaws.{REGION}.{svc}",
                    VpcEndpointType="Interface", SubnetIds=[subs[0]], SecurityGroupIds=[sg], PrivateDnsEnabled=True,
                    TagSpecifications=tag_spec("vpc-endpoint"))["VpcEndpoint"]["VpcEndpointId"]
            except ClientError as e:
                check(f"endpoint_{svc}_created", False, str(e))
                res["error"] = str(e)
        ids["endpoints"] = eps

        def avail():
            d = ec2.describe_vpc_endpoints(VpcEndpointIds=list(eps.values()))["VpcEndpoints"]
            return d if all(x["State"] == "available" for x in d) else None
        d = wait_until(avail, 600, 10)
    check("endpoints_available", bool(d), {k: v for k, v in eps.items()})
    if not d:
        return

    # ECR + image
    with timed("ecr_build_push"):
        repo = ecr.create_repository(repositoryName=NAMES["repo"], tags=[{"Key": t["Key"], "Value": t["Value"]} for t in TAGS])["repository"]
        image = build_push(repo["repositoryUri"])
    ids["image"] = image
    check("image_pushed_arm64", True, image)

    # ECS
    ecs.create_cluster(clusterName=NAMES["cluster"], tags=[{"key": "project", "value": "paynest-probe"}, {"key": "probe", "value": PROBE}])
    time.sleep(10)  # IAM propagation

    def taskdef(fam, cpu, mem):
        return ecs.register_task_definition(family=fam, requiresCompatibilities=["FARGATE"], networkMode="awsvpc",
            cpu=cpu, memory=mem, executionRoleArn=ex, taskRoleArn=tk,
            runtimePlatform=dict(cpuArchitecture="ARM64", operatingSystemFamily="LINUX"),
            tags=[{"key": "project", "value": "paynest-probe"}, {"key": "probe", "value": PROBE}],
            containerDefinitions=[dict(name="app", image=image, essential=True,
                portMappings=[dict(containerPort=8080, protocol="tcp")],
                environment=[dict(name="MODEL_ID", value=MODEL_ID), dict(name="QUEUE_URL", value=q),
                             dict(name="AWS_REGION", value=REGION)],
                logConfiguration=dict(logDriver="awslogs", options={"awslogs-group": NAMES["lg"],
                    "awslogs-region": REGION, "awslogs-stream-prefix": "app"}))])["taskDefinition"]["taskDefinitionArn"]

    def run_task(td):
        r = ecs.run_task(cluster=NAMES["cluster"], taskDefinition=td, launchType="FARGATE", count=1,
            networkConfiguration=dict(awsvpcConfiguration=dict(subnets=[subs[0]], securityGroups=[sg], assignPublicIp="DISABLED")),
            tags=[{"key": "project", "value": "paynest-probe"}, {"key": "probe", "value": PROBE}])
        if r["failures"]:
            raise RuntimeError(str(r["failures"]))
        return r["tasks"][0]["taskArn"]

    def wait_running(arn, timeout=420):
        def f():
            t = ecs.describe_tasks(cluster=NAMES["cluster"], tasks=[arn])["tasks"][0]
            if t["lastStatus"] in ("RUNNING", "STOPPED"):
                return t
        return wait_until(f, timeout, 5)

    with timed("task_a_running"):
        tdA = taskdef(f"{P}-task-a", "512", "1024")
        A = run_task(tdA)
        tA = wait_running(A)
    ipA = next((x["value"] for a in tA["attachments"] for x in a["details"] if x["name"] == "privateIPv4Address"), None)
    ids.update(task_a=A, task_a_ip=ipA)
    check("task_a_running", tA["lastStatus"] == "RUNNING",
          f"{A} lastStatus={tA['lastStatus']} stopped={tA.get('stoppedReason')} ip={ipA}")
    with timed("task_b_running"):
        tdB = taskdef(f"{P}-task-b", "2048", "8192")
        B = run_task(tdB)
        tB = wait_running(B)
    ids["task_b"] = B
    check("task_b_2vcpu_8gb_running", tB["lastStatus"] == "RUNNING", f"{B} lastStatus={tB['lastStatus']} stopped={tB.get('stoppedReason')}")

    # logs for Task A
    def getlogs():
        ev = logs.filter_log_events(logGroupName=NAMES["lg"], logStreamNamePrefix="app/app/" + A.split("/")[-1])["events"]
        m = [e["message"].strip() for e in ev]
        return m if any(x.startswith("SQS_") for x in m) else None
    lines = wait_until(getlogs, 120, 8) or []
    if not lines:
        lines = [e["message"].strip() for e in logs.filter_log_events(logGroupName=NAMES["lg"])["events"]]
    ids["task_a_log_lines"] = lines
    check("task_a_bedrock_ok_private", any(x.startswith("BEDROCK_OK") for x in lines), [x for x in lines if "BEDROCK" in x])
    check("task_a_sqs_ok_private", "SQS_OK" in lines, [x for x in lines if "SQS" in x])
    if any(x.startswith("BEDROCK_FAIL") for x in lines):
        res["error"] = next(x for x in lines if x.startswith("BEDROCK_FAIL"))
    # verify message landed
    msgs = sqs.receive_message(QueueUrl=q, WaitTimeSeconds=5, MaxNumberOfMessages=1).get("Messages", [])
    check("sqs_message_from_fargate_received", bool(msgs), msgs[0]["Body"] if msgs else "none")

    # ALB
    if ipA:
        with timed("alb_healthy"):
            lb = elb.create_load_balancer(Name=NAMES["alb"], Subnets=subs, SecurityGroups=[sg], Scheme="internal",
                                          Type="application", Tags=TAGS)["LoadBalancers"][0]
            tg = elb.create_target_group(Name=NAMES["tg"], Protocol="HTTP", Port=8080, VpcId=vpc, TargetType="ip",
                HealthCheckPath="/health", HealthCheckIntervalSeconds=10, HealthyThresholdCount=2,
                Tags=TAGS)["TargetGroups"][0]["TargetGroupArn"]
            elb.create_listener(LoadBalancerArn=lb["LoadBalancerArn"], Protocol="HTTP", Port=80,
                                DefaultActions=[dict(Type="forward", TargetGroupArn=tg)])
            elb.register_targets(TargetGroupArn=tg, Targets=[dict(Id=ipA, Port=8080)])
            ids.update(alb=lb["LoadBalancerArn"], alb_scheme=lb["Scheme"], tg=tg)

            def healthy():
                d = elb.describe_target_health(TargetGroupArn=tg)["TargetHealthDescriptions"]
                return d if d and d[0]["TargetHealth"]["State"] == "healthy" else None
            h = wait_until(healthy, 480, 10)
        st = elb.describe_target_health(TargetGroupArn=tg)["TargetHealthDescriptions"]
        check("alb_internal_target_healthy", bool(h), st[0]["TargetHealth"] if st else "no targets")
    res["latency_ms"]["total"] = int((time.time() - t0) * 1000)


def main():
    if "--cleanup" in sys.argv:
        cleanup(); print("cleanup done"); return
    t0 = time.time()
    try:
        run()
    except ClientError as e:
        res["error"] = str(e)
        code = e.response["Error"]["Code"]
        denied = any(k in code or k in str(e) for k in ("AccessDenied", "UnauthorizedOperation", "explicit deny", "not subscribed", "SubscriptionRequired", "UnsupportedOperation"))
        check("unexpected_aws_error", False, str(e))
        res["_denied"] = denied
    except Exception as e:
        res["error"] = repr(e)
        check("unexpected_error", False, repr(e))
    finally:
        try:
            cleanup()
        except Exception as e:
            res["notes"].append(f"cleanup exception: {e!r}")
        mins = (time.time() - t0) / 60
        # rough: 7 interface endpoints @ ~0.012/h, ALB 0.027/h, fargate small, ECR/logs negligible
        res["est_cost_usd"] = round(mins / 60 * (7 * 0.012 + 0.027 + 0.08) + 0.01, 3)
        res["notes"].append(f"Model {MODEL_ID} used because eu.anthropic.claude-haiku-5-5 returned AccessDeniedException "
                            "'anthropic.claude-haiku-5-5 is not available for this account' (account model access, not VPC).")
        allpass = res["checks"] and all(c["pass_"] for c in res["checks"])
        denied = res.pop("_denied", False)
        res["status"] = "PASS" if allpass else ("BLOCKED" if denied else "FAIL")
        if res["status"] == "BLOCKED":
            res["fallback"] = "Use a public subnet + NAT-less alternative is not allowed; move the blocked service behind another path or request advanced features."
        for c in res["checks"]:
            c["pass"] = c.pop("pass_")
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(res, indent=2, default=str))
        print("STATUS", res["status"], "->", RESULT)


if __name__ == "__main__":
    main()
