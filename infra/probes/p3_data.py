#!/usr/bin/env python3
"""P3: RDS PostgreSQL (pgvector) + ElastiCache Valkey probed from inside the VPC via a Fargate runner task.

Run:      AWS_PROFILE=paynest uv run --python 3.12 --with "boto3[crt]" infra/probes/p3_data.py
Cleanup:  ... infra/probes/p3_data.py --cleanup
All resource names are fixed (paynest-probe-*) so cleanup is idempotent and name based.
"""
import json, os, subprocess, sys, time, threading
from pathlib import Path
import boto3
from botocore.exceptions import ClientError, WaiterError

REGION = "eu-north-1"
PROBE = "p3_data"
HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
S = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "paynest"), region_name=REGION)
ec2, rds, ecc, ecs, ecr, iam, logs, sm, sts = (S.client(x) for x in
    ["ec2", "rds", "elasticache", "ecs", "ecr", "iam", "logs", "secretsmanager", "sts"])
ACCOUNT = sts.get_caller_identity()["Account"]

N = "paynest-probe-"
RDS_ID, VK_ID = N + "pg", N + "vk"
DB_SNG, VK_SNG = N + "data-db-sng", N + "data-vk-sng"
SG_RUNNER, SG_RDS, SG_CACHE = N + "data-runner-sg", N + "data-rds-sg", N + "data-cache-sg"
CLUSTER, REPO, LOGGRP, ROLE, FAMILY = N + "data", N + "data-runner", "/paynest-probe/data", N + "data-exec", N + "data-runner"
VPC_NAME = N + "data-vpc"
TAGS_KV = [("project", "paynest-probe"), ("probe", PROBE)]
EC2_TAGS = [{"Key": k, "Value": v} for k, v in TAGS_KV]
GEN_TAGS = [{"Key": k, "Value": v} for k, v in TAGS_KV]  # RDS / ElastiCache / ECS / ECR / IAM share Key/Value shape
ECS_TAGS = [{"key": k, "value": v} for k, v in TAGS_KV]

R = {"status": "FAIL", "checks": [], "ids_resolved": {}, "latency_ms": {}, "est_cost_usd": 0.0, "error": None, "fallback": None}
T0 = time.time()


def log(*a):
    print(f"[{time.time()-T0:6.0f}s]", *a, flush=True)


def tag_spec(rtype):
    return [{"ResourceType": rtype, "Tags": EC2_TAGS}]


def ok_missing(fn, *codes):
    try:
        return fn()
    except ClientError as e:
        if e.response["Error"]["Code"] in codes or any(c in str(e) for c in codes):
            return None
        raise


# ---------------------------------------------------------------- network
def get_network():
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    created = False
    if vpcs:
        vpc = vpcs[0]["VpcId"]
        subs = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}, {"Name": "default-for-az", "Values": ["true"]}])["Subnets"]
        by_az = {}
        for s in subs:
            by_az.setdefault(s["AvailabilityZoneId"], s["SubnetId"])
        subnets = list(by_az.values())[:2]
        if len(subnets) < 2:
            raise RuntimeError("default VPC has <2 default subnets")
        log("default VPC", vpc, subnets)
    else:
        log("no default VPC; creating", VPC_NAME)
        vpc = ec2.create_vpc(CidrBlock="10.77.0.0/16", TagSpecifications=tag_spec("vpc") + [] )["Vpc"]["VpcId"]
        ec2.create_tags(Resources=[vpc], Tags=[{"Key": "Name", "Value": VPC_NAME}])
        ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsSupport={"Value": True})
        ec2.modify_vpc_attribute(VpcId=vpc, EnableDnsHostnames={"Value": True})
        igw = ec2.create_internet_gateway(TagSpecifications=tag_spec("internet-gateway"))["InternetGateway"]["InternetGatewayId"]
        ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=vpc)
        rt = ec2.create_route_table(VpcId=vpc, TagSpecifications=tag_spec("route-table"))["RouteTable"]["RouteTableId"]
        ec2.create_route(RouteTableId=rt, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw)
        azs = [z["ZoneName"] for z in ec2.describe_availability_zones()["AvailabilityZones"]][:2]
        subnets = []
        for i, az in enumerate(azs):
            s = ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.77.{i}.0/24", AvailabilityZone=az, TagSpecifications=tag_spec("subnet"))["Subnet"]["SubnetId"]
            ec2.modify_subnet_attribute(SubnetId=s, MapPublicIpOnLaunch={"Value": True})
            ec2.associate_route_table(RouteTableId=rt, SubnetId=s)
            subnets.append(s)
        created = True
    R["ids_resolved"].update(vpc=vpc, subnets=subnets, vpc_created_by_probe=created)
    return vpc, subnets


def mk_sg(vpc, name, desc):
    sg = ec2.create_security_group(GroupName=name, Description=desc, VpcId=vpc, TagSpecifications=tag_spec("security-group") + [])["GroupId"]
    ec2.create_tags(Resources=[sg], Tags=[{"Key": "Name", "Value": name}])
    return sg


def make_sgs(vpc):
    runner = mk_sg(vpc, SG_RUNNER, "paynest-probe runner egress only")
    rdssg = mk_sg(vpc, SG_RDS, "paynest-probe rds 5432 from runner")
    cache = mk_sg(vpc, SG_CACHE, "paynest-probe cache 6379 from runner")
    for sg, port in ((rdssg, 5432), (cache, 6379)):
        ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": port, "ToPort": port, "UserIdGroupPairs": [{"GroupId": runner}]}])
    # runner SG: default egress-all rule is kept; no ingress rules at all
    R["ids_resolved"].update(sg_runner=runner, sg_rds=rdssg, sg_cache=cache)
    return runner, rdssg, cache


# ---------------------------------------------------------------- data stores
def pick_pg_version():
    vs = [v["EngineVersion"] for v in rds.describe_db_engine_versions(Engine="postgres")["DBEngineVersions"]]
    for major in ("17", "16"):
        c = sorted([v for v in vs if v.split(".")[0] == major], key=lambda v: [int(x) for x in v.split(".")])
        if c:
            return c[-1]
    raise RuntimeError("no PG 17/16")


def create_rds(subnets, sg):
    t = time.time()
    ver = pick_pg_version()
    rds.create_db_subnet_group(DBSubnetGroupName=DB_SNG, DBSubnetGroupDescription="paynest-probe", SubnetIds=subnets, Tags=GEN_TAGS)
    r = rds.create_db_instance(
        DBInstanceIdentifier=RDS_ID, Engine="postgres", EngineVersion=ver, DBInstanceClass="db.t4g.micro",
        AllocatedStorage=20, StorageType="gp3", MultiAZ=False, PubliclyAccessible=False,
        MasterUsername="pnprobe", ManageMasterUserPassword=True, BackupRetentionPeriod=0, DeletionProtection=False,
        DBSubnetGroupName=DB_SNG, VpcSecurityGroupIds=[sg], StorageEncrypted=True, Tags=GEN_TAGS)["DBInstance"]
    R["ids_resolved"].update(rds_engine_version=ver, rds_secret_arn=r["MasterUserSecret"]["SecretArn"])
    log("RDS creating", ver)
    rds.get_waiter("db_instance_available").wait(DBInstanceIdentifier=RDS_ID, WaiterConfig={"Delay": 30, "MaxAttempts": 60})
    d = rds.describe_db_instances(DBInstanceIdentifier=RDS_ID)["DBInstances"][0]
    R["ids_resolved"]["rds_endpoint"] = d["Endpoint"]["Address"]
    R["latency_ms"]["rds_create_to_available"] = int((time.time() - t) * 1000)
    log("RDS available", d["Endpoint"]["Address"])


def create_valkey(subnets, sg):
    t = time.time()
    try:
        ecc.create_cache_subnet_group(CacheSubnetGroupName=VK_SNG, CacheSubnetGroupDescription="paynest-probe", SubnetIds=subnets, Tags=GEN_TAGS)
        ecc.create_replication_group(
            ReplicationGroupId=VK_ID, ReplicationGroupDescription="paynest-probe valkey", Engine="valkey", EngineVersion="9.0",
            CacheNodeType="cache.t4g.micro", NumCacheClusters=1, AutomaticFailoverEnabled=False, MultiAZEnabled=False,
            CacheSubnetGroupName=VK_SNG, SecurityGroupIds=[sg], TransitEncryptionEnabled=True, AtRestEncryptionEnabled=True,
            Tags=GEN_TAGS)
        log("Valkey creating")
        ecc.get_waiter("replication_group_available").wait(ReplicationGroupId=VK_ID, WaiterConfig={"Delay": 30, "MaxAttempts": 60})
        g = ecc.describe_replication_groups(ReplicationGroupId=VK_ID)["ReplicationGroups"][0]
        ep = g["NodeGroups"][0]["PrimaryEndpoint"]
        R["ids_resolved"].update(valkey_endpoint=ep["Address"], valkey_port=ep["Port"], valkey_engine="valkey 9.0")
        R["latency_ms"]["valkey_create_to_available"] = int((time.time() - t) * 1000)
        log("Valkey available", ep["Address"])
    except ClientError as e:
        R["valkey_blocked"] = {"error": str(e)}
        log("Valkey BLOCKED/failed:", e)
    except WaiterError as e:
        R["valkey_blocked"] = {"error": f"waiter: {e}"}


# ---------------------------------------------------------------- image
def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def build_push():
    t = time.time()
    ecr.create_repository(repositoryName=REPO, tags=[{"Key": k, "Value": v} for k, v in TAGS_KV], imageScanningConfiguration={"scanOnPush": False})
    reg = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
    tok = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
    import base64
    pw = base64.b64decode(tok).decode().split(":", 1)[1]
    subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", reg], input=pw, check=True, capture_output=True, text=True)
    img = f"{reg}/{REPO}:latest"
    sh(["docker", "build", "--platform", "linux/arm64", "--provenance=false", "-t", img, str(HERE / "p3_runner")])
    sh(["docker", "push", img])
    R["ids_resolved"]["image"] = img
    R["latency_ms"]["image_build_push"] = int((time.time() - t) * 1000)
    log("image pushed", img)
    return img


# ---------------------------------------------------------------- ECS
def create_role(secret_arn):
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"},
             "Action": "sts:AssumeRole", "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}}}]}
    arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust), Tags=GEN_TAGS)["Role"]["Arn"]
    pol = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
         "Resource": f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/{REPO}"},
        {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{LOGGRP}:*"},
        {"Effect": "Allow", "Action": "secretsmanager:GetSecretValue", "Resource": secret_arn}]}
    iam.put_role_policy(RoleName=ROLE, PolicyName="exec", PolicyDocument=json.dumps(pol))
    R["ids_resolved"]["exec_role"] = arn
    time.sleep(12)  # IAM propagation
    return arn


def run_task(img, role_arn, subnets, runner_sg):
    t = time.time()
    logs.create_log_group(logGroupName=LOGGRP, tags={k: v for k, v in TAGS_KV})
    logs.put_retention_policy(logGroupName=LOGGRP, retentionInDays=1)
    ecs.create_cluster(clusterName=CLUSTER, tags=ECS_TAGS)
    env = [{"name": "PGHOST", "value": R["ids_resolved"]["rds_endpoint"]}, {"name": "PGUSER", "value": "pnprobe"}]
    if R["ids_resolved"].get("valkey_endpoint"):
        env += [{"name": "VALKEY_HOST", "value": R["ids_resolved"]["valkey_endpoint"]},
                {"name": "VALKEY_PORT", "value": str(R["ids_resolved"]["valkey_port"])}]
    td = ecs.register_task_definition(
        family=FAMILY, requiresCompatibilities=["FARGATE"], networkMode="awsvpc", cpu="512", memory="1024",
        runtimePlatform={"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"}, executionRoleArn=role_arn,
        containerDefinitions=[{"name": "runner", "image": img, "essential": True, "environment": env,
            "secrets": [{"name": "PGPASSWORD", "valueFrom": R["ids_resolved"]["rds_secret_arn"] + ":password::"}],
            "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": LOGGRP, "awslogs-region": REGION, "awslogs-stream-prefix": "runner"}}}],
        tags=ECS_TAGS)["taskDefinition"]["taskDefinitionArn"]
    R["ids_resolved"]["task_def"] = td
    task = ecs.run_task(cluster=CLUSTER, taskDefinition=td, launchType="FARGATE", count=1, tags=ECS_TAGS,
        networkConfiguration={"awsvpcConfiguration": {"subnets": subnets, "securityGroups": [runner_sg], "assignPublicIp": "ENABLED"}})
    if task.get("failures"):
        raise RuntimeError(f"run_task failures: {task['failures']}")
    arn = task["tasks"][0]["taskArn"]
    R["ids_resolved"]["task"] = arn
    log("task started", arn.split("/")[-1])
    ecs.get_waiter("tasks_stopped").wait(cluster=CLUSTER, tasks=[arn], WaiterConfig={"Delay": 10, "MaxAttempts": 90})
    d = ecs.describe_tasks(cluster=CLUSTER, tasks=[arn])["tasks"][0]
    c = d["containers"][0]
    R["ids_resolved"]["task_stop"] = {"stoppedReason": d.get("stoppedReason"), "exitCode": c.get("exitCode"), "reason": c.get("reason")}
    R["latency_ms"]["task_run"] = int((time.time() - t) * 1000)
    time.sleep(5)
    lines, tok = [], None
    while True:
        kw = dict(logGroupName=LOGGRP)
        if tok:
            kw["nextToken"] = tok
        r = logs.filter_log_events(**kw)
        lines += [e["message"] for e in r["events"]]
        tok = r.get("nextToken")
        if not tok:
            break
    return lines


# ---------------------------------------------------------------- teardown
def wait_gone(fn, label, tries=90, delay=20):
    for _ in range(tries):
        if not fn():
            return True
        time.sleep(delay)
    log("WARN still present:", label)
    return False


def teardown():
    log("teardown start")
    # ECS
    try:
        for fam in ecs.list_task_definitions(familyPrefix=FAMILY)["taskDefinitionArns"]:
            ecs.deregister_task_definition(taskDefinition=fam)
            try:
                ecs.delete_task_definitions(taskDefinitions=[fam])
            except Exception as e:
                log("delete_task_definitions (non-fatal):", str(e)[:120])
        cl = ecs.describe_clusters(clusters=[CLUSTER])["clusters"]
        if cl and cl[0]["status"] == "ACTIVE":
            for t in ecs.list_tasks(cluster=CLUSTER)["taskArns"]:
                ecs.stop_task(cluster=CLUSTER, task=t)
            ecs.delete_cluster(cluster=CLUSTER)
    except Exception as e:
        log("ecs teardown:", e)
    # RDS + Valkey deletion in parallel
    def del_rds():
        try:
            rds.delete_db_instance(DBInstanceIdentifier=RDS_ID, SkipFinalSnapshot=True, DeleteAutomatedBackups=True)
        except ClientError as e:
            if e.response["Error"]["Code"] == "InvalidDBInstanceState":
                pass
            elif e.response["Error"]["Code"] != "DBInstanceNotFound":
                raise
        wait_gone(lambda: ok_missing(lambda: rds.describe_db_instances(DBInstanceIdentifier=RDS_ID)["DBInstances"], "DBInstanceNotFound"), "rds")
        ok_missing(lambda: rds.delete_db_subnet_group(DBSubnetGroupName=DB_SNG), "DBSubnetGroupNotFoundFault", "DBSubnetGroupNotFound")

    def del_vk():
        try:
            ecc.delete_replication_group(ReplicationGroupId=VK_ID, RetainPrimaryCluster=False)
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("ReplicationGroupNotFoundFault", "InvalidReplicationGroupState"):
                raise
        wait_gone(lambda: ok_missing(lambda: ecc.describe_replication_groups(ReplicationGroupId=VK_ID)["ReplicationGroups"], "ReplicationGroupNotFoundFault"), "valkey")
        ok_missing(lambda: ecc.delete_cache_subnet_group(CacheSubnetGroupName=VK_SNG), "CacheSubnetGroupNotFoundFault", "CacheSubnetGroupNotFound")

    ths = [threading.Thread(target=f) for f in (del_rds, del_vk)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    # ECR / IAM / logs
    ok_missing(lambda: ecr.delete_repository(repositoryName=REPO, force=True), "RepositoryNotFoundException")
    try:
        for p in iam.list_role_policies(RoleName=ROLE)["PolicyNames"]:
            iam.delete_role_policy(RoleName=ROLE, PolicyName=p)
        iam.delete_role(RoleName=ROLE)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            log("iam:", e)
    ok_missing(lambda: logs.delete_log_group(logGroupName=LOGGRP), "ResourceNotFoundException")
    # SGs (retry while ENIs release)
    names = [SG_RUNNER, SG_RDS, SG_CACHE]
    for _ in range(40):
        sgs = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": names}])["SecurityGroups"]
        if not sgs:
            break
        for g in sgs:
            try:
                ec2.delete_security_group(GroupId=g["GroupId"])
            except ClientError:
                pass
        time.sleep(15)
    # VPC (only if probe-created)
    for v in ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [VPC_NAME]}])["Vpcs"]:
        vid = v["VpcId"]
        for rt in ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vid]}])["RouteTables"]:
            for a in rt["Associations"]:
                if not a["Main"]:
                    ec2.disassociate_route_table(AssociationId=a["RouteTableAssociationId"])
            if not any(a["Main"] for a in rt["Associations"]):
                ec2.delete_route_table(RouteTableId=rt["RouteTableId"])
        for s in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vid]}])["Subnets"]:
            ec2.delete_subnet(SubnetId=s["SubnetId"])
        for i in ec2.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vid]}])["InternetGateways"]:
            ec2.detach_internet_gateway(InternetGatewayId=i["InternetGatewayId"], VpcId=vid)
            ec2.delete_internet_gateway(InternetGatewayId=i["InternetGatewayId"])
        ec2.delete_vpc(VpcId=vid)
    log("teardown done")


# ---------------------------------------------------------------- main
def parse(lines):
    out = {}
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                j = json.loads(ln)
                out[j["check"]] = j
            except Exception:
                pass
    return out, any("RUNNER_DONE" in ln for ln in lines)


def main():
    if "--cleanup" in sys.argv:
        teardown()
        return
    est_start = time.time()
    lines = []
    try:
        vpc, subnets = get_network()
        runner_sg, rds_sg, cache_sg = make_sgs(vpc)
        ths = [threading.Thread(target=create_rds, args=(subnets, rds_sg))]
        errs = {}

        def wrap(f, *a):
            try:
                f(*a)
            except Exception as e:
                errs[f.__name__] = e
        ths = [threading.Thread(target=wrap, args=(create_rds, subnets, rds_sg)),
               threading.Thread(target=wrap, args=(create_valkey, subnets, cache_sg))]
        [t.start() for t in ths]
        img = build_push()
        [t.join() for t in ths]
        if "create_rds" in errs:
            raise errs["create_rds"]
        if "create_valkey" in errs:
            R["valkey_blocked"] = {"error": str(errs["create_valkey"])}
        role = create_role(R["ids_resolved"]["rds_secret_arn"])
        lines = run_task(img, role, subnets, runner_sg)
    except Exception as e:
        R["error"] = f"{type(e).__name__}: {e}"
        log("ERROR", R["error"])
    finally:
        try:
            teardown()
        except Exception as e:
            log("TEARDOWN ERROR", e)
            R["error"] = (R["error"] or "") + f" | teardown: {e}"
    wall_h = (time.time() - est_start) / 3600
    R["est_cost_usd"] = round(wall_h * (0.016 + 0.0128 + 0.02 * 0.25 + 0.0005 + 0.0003) + 0.003, 3)  # rds+valkey+fargate-ish+storage
    R["wall_seconds"] = int(time.time() - est_start)
    checks, done = parse(lines)
    R["raw_log_lines"] = [ln for ln in lines if ln.strip().startswith("{") or "RUNNER_DONE" in ln]
    for name in ("p3_secrets_via_role", "pg_connect", "a_pgvector_extension", "b_hnsw_nn_query", "c_tsvector_websearch", "d_rls", "e_valkey"):
        c = checks.get(name)
        R["checks"].append({"name": name, "pass": bool(c and c["pass"]), "evidence": c if c else "no output"})
    R["checks"].append({"name": "f_runner_done", "pass": done, "evidence": "RUNNER_DONE printed" if done else "missing"})
    pg_names = [c for c in R["checks"] if c["name"] != "e_valkey"]
    pg_ok = all(c["pass"] for c in pg_names)
    vk = next(c for c in R["checks"] if c["name"] == "e_valkey")
    RESULTS.mkdir(exist_ok=True)
    common = {"ids_resolved": R["ids_resolved"], "latency_ms": R["latency_ms"], "est_cost_usd": R["est_cost_usd"], "wall_seconds": R["wall_seconds"]}
    pg = {"status": "PASS" if pg_ok else "FAIL", "checks": pg_names, "error": R["error"], "fallback": None, **common}
    if "valkey_blocked" in R:
        vkr = {"status": "BLOCKED", "checks": [vk], "error": R["valkey_blocked"]["error"],
               "fallback": "Postgres-only cache (UNLOGGED table + TTL column)", **common}
    else:
        vkr = {"status": "PASS" if vk["pass"] else "FAIL", "checks": [vk], "error": R["error"] if not vk["pass"] else None, "fallback": None, **common}
    (RESULTS / "p3_rds_pgvector.json").write_text(json.dumps({**pg, "raw_log_lines": R["raw_log_lines"]}, indent=2, default=str))
    (RESULTS / "p3_valkey.json").write_text(json.dumps(vkr, indent=2, default=str))
    (RESULTS / "p3_secrets_via_role.json").write_text(json.dumps(
        {"status": "PASS" if (R["checks"][0]["pass"] and pg_names[1]["pass"]) else "FAIL", "checks": [R["checks"][0], pg_names[1]],
         "error": R["error"], "fallback": None, **common}, indent=2, default=str))
    print(json.dumps({"rds": pg["status"], "valkey": vkr["status"], "cost": R["est_cost_usd"]}))


if __name__ == "__main__":
    main()
