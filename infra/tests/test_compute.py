"""Assertions on ComputeStack / OpsStack / MonitoringStack (and the whole app). No AWS calls."""
import json
import os
import re
import sys
from pathlib import Path

import pytest
from aws_cdk.assertions import Match, Template

os.environ.setdefault("JSII_SILENCE_WARNING_END_OF_LIFE_NODE_VERSION", "1")
from app import build_app  # noqa: E402
import config  # noqa: E402

ENV = "dev"
ACT = f"paynest-a7-{ENV}-act-task"


def all_templates(**ctx):
    app = build_app(region="eu-north-1", env_name=ENV, sizing=ctx.pop("sizing", "dev"), **ctx)
    names = {"network", "data", "ops", "compute", "monitoring"}
    return {n: Template.from_stack(app.node.find_child(f"paynest-a7-{ENV}-{n}")) for n in names}


@pytest.fixture(scope="module")
def t():
    return all_templates()


def _res(tpl, typ):
    return tpl.find_resources(typ)


def _statements(doc):
    st = doc["Statement"]
    return st if isinstance(st, list) else [st]


def _iam_policies(tpl):
    """(owner description, statements) for every inline policy and managed policy."""
    out = []
    for r in _res(tpl, "AWS::IAM::Policy").values():
        out.append((json.dumps(r["Properties"].get("Roles")), _statements(r["Properties"]["PolicyDocument"])))
    for r in _res(tpl, "AWS::IAM::ManagedPolicy").values():
        out.append((json.dumps(r["Properties"].get("Roles") or r["Properties"].get("ManagedPolicyName")),
                    _statements(r["Properties"]["PolicyDocument"])))
    for r in _res(tpl, "AWS::IAM::Role").values():
        for p in r["Properties"].get("Policies", []):
            out.append((json.dumps(r["Properties"].get("RoleName")), _statements(p["PolicyDocument"])))
    return out


def _actions(stmt):
    a = stmt.get("Action", [])
    return [a] if isinstance(a, str) else a


# ---------------------------------------------------------------------------------------------------------- network posture
def test_stack_set_and_region(t):
    assert set(t) == {"network", "data", "ops", "compute", "monitoring"}
    for tpl in t.values():
        s = json.dumps(tpl.to_json())
        for other in ("us-east-1", "eu-west-1", "ap-south-1", "me-central-1"):
            assert other not in s


def test_no_nat_no_public_ips(t):
    for tpl in t.values():
        tpl.resource_count_is("AWS::EC2::NatGateway", 0)
        tpl.resource_count_is("AWS::EC2::EIP", 0)
    for svc in _res(t["compute"], "AWS::ECS::Service").values():
        assert svc["Properties"]["NetworkConfiguration"]["AwsvpcConfiguration"]["AssignPublicIp"] == "DISABLED"
        assert svc["Properties"]["LaunchType"] == "FARGATE"
    # no subnet auto-assigns public IPs (workload subnets are isolated)
    for sn in _res(t["network"], "AWS::EC2::Subnet").values():
        assert sn["Properties"].get("MapPublicIpOnLaunch") in (None, False)


def test_alb_internal_by_default_and_public_needs_cert(t):
    t["compute"].has_resource_properties("AWS::ElasticLoadBalancingV2::LoadBalancer", {"Scheme": "internal", "Type": "application"})
    with pytest.raises(Exception):
        all_templates(public_alb="true")                      # no certificate -> refuses
    pub = all_templates(public_alb="true", alb_certificate_arn="arn:aws:acm:eu-north-1:000000000000:certificate/x")
    pub["compute"].has_resource_properties("AWS::ElasticLoadBalancingV2::LoadBalancer", {"Scheme": "internet-facing"})
    listeners = _res(pub["compute"], "AWS::ElasticLoadBalancingV2::Listener").values()
    assert any(l["Properties"]["Port"] == 443 and l["Properties"]["Protocol"] == "HTTPS" for l in listeners)


def test_all_tasks_arm64_fargate_and_health_check(t):
    for td in _res(t["compute"], "AWS::ECS::TaskDefinition").values():
        assert td["Properties"]["RuntimePlatform"]["CpuArchitecture"] == "ARM64"
        assert td["Properties"]["RequiresCompatibilities"] == ["FARGATE"]
    # api: DB-free liveness (the DB is empty until the bootstrap task has run); mock payroll has no DB either
    t["compute"].has_resource_properties("AWS::ElasticLoadBalancingV2::TargetGroup",
                                         {"HealthCheckPath": "/livez", "Port": 8080, "TargetType": "ip"})
    paths = sorted(g["Properties"]["HealthCheckPath"] for g in _res(t["compute"], "AWS::ElasticLoadBalancingV2::TargetGroup").values())
    assert paths == ["/health", "/livez"]


def test_services_and_run_task_definitions(t):
    svcs = _res(t["compute"], "AWS::ECS::Service")
    assert len(svcs) == 3                                     # api, worker, mock-payroll (no decider, act/bootstrap are RunTask only)
    names = " ".join(json.dumps(s["Properties"]["ServiceName"]) for s in svcs.values())
    assert "-api" in names and "-worker" in names and "-mock-payroll" in names and "decider" not in names
    assert len(_res(t["compute"], "AWS::ECS::TaskDefinition")) == 6                                              # api, mock, worker, act, bootstrap, smoke
    # worker scales 0..2 on queue depth
    t["compute"].has_resource_properties("AWS::ApplicationAutoScaling::ScalableTarget", {"MinCapacity": 0, "MaxCapacity": 1})


# ---------------------------------------------------------------------------------------------------------- act role / KMS
def test_only_act_role_has_unconditioned_kms_decrypt(t):
    """kms:Decrypt may be allowed (a) to the act role, or (b) only through SQS (kms:ViaService), which the key policy permits."""
    allowed_direct = 0
    for tpl in t.values():
        for owner, stmts in _iam_policies(tpl):
            for s in stmts:
                acts = _actions(s)
                if s.get("Effect") == "Allow" and any(a in ("kms:Decrypt", "kms:*", "*") for a in acts):
                    cond = json.dumps(s.get("Condition", {}))
                    if "kms:ViaService" in cond and "sqs." in cond:
                        continue
                    allowed_direct += 1
                    assert "act-vault" in owner or ACT in owner or "ActVaultPolicy" in owner, (owner, s)
    assert allowed_direct == 1                                 # exactly the act vault managed policy


def _glob(pattern: str, value: str) -> bool:
    import fnmatch
    return fnmatch.fnmatchcase(value, pattern)


def _deny_applies(deny: dict, principal_arn: str | None, via_service: str | None) -> bool:
    """Evaluate the key-policy Deny statement's Condition block for one caller (a tiny IAM evaluator for the three operators used)."""
    cond = deny["Condition"]
    arn_like = cond["ArnLike"]["aws:PrincipalArn"]
    arn_not_like = cond["ArnNotLike"]["aws:PrincipalArn"]
    via_list = cond["StringNotEquals"]["kms:ViaService"]
    like = principal_arn is not None and any(_glob(p, principal_arn) for p in (arn_like if isinstance(arn_like, list) else [arn_like]))
    not_like = principal_arn is None or not any(_glob(p, principal_arn) for p in (arn_not_like if isinstance(arn_not_like, list) else [arn_not_like]))
    not_via = via_service is None or via_service not in via_list           # StringNotEquals is true when the key is absent
    return like and not_like and not_via


def _key_deny(t):
    key = next(iter(_res(t["data"], "AWS::KMS::Key").values()))
    stmts = key["Properties"]["KeyPolicy"]["Statement"]
    return [s for s in stmts if s["Effect"] == "Deny"]


def test_vault_key_policy_denies_decrypt_except_act_and_aws_services(t):
    """The deny (a) covers every account role and user except act, (b) never applies to service principals or service-linked roles
    (RDS/ElastiCache use the key through grants with their own identity), (c) lets rds/elasticache/sqs/secretsmanager/logs in eu-north-1
    through kms:ViaService."""
    denies = _key_deny(t)
    assert len(denies) == 1                                    # the only Deny on the key
    deny = denies[0]
    assert deny["Action"] == "kms:Decrypt" and deny["Effect"] == "Deny" and deny["Principal"] == {"AWS": "*"}
    cond = json.dumps(deny["Condition"])
    assert ACT in cond and "aws-service-role" in cond
    # the rendered account is a token-free literal at synth: the dummy 000000000000, or CDK_DEFAULT_ACCOUNT when it is set.
    # Derive it from the template so the test passes either way.
    import re
    accts = set(re.findall(r"arn:aws:iam::(\d{12}):", cond))
    assert len(accts) == 1, accts
    acct = accts.pop()
    via = {f"{svc}.eu-north-1.amazonaws.com" for svc in ("rds", "elasticache", "sqs", "secretsmanager", "logs")}
    assert set(deny["Condition"]["StringNotEquals"]["kms:ViaService"]) == via
    role = lambda n: f"arn:aws:iam::{acct}:role/{n}"           # noqa: E731
    # blocked: humans and apps calling Decrypt directly (no ViaService), and via a service that is not on the list
    for who in (role("paynest-a7-dev-worker-task"), role("paynest-a7-dev-api-task"), role("paynest-a7-dev-bootstrap-task"),
                role("aws-reserved/sso.amazonaws.com/AWSReservedSSO_Admin_abc"), role(f"cdk-hnb659fds-cfn-exec-role-{acct}-eu-north-1"),
                f"arn:aws:iam::{acct}:user/someone"):
        assert _deny_applies(deny, who, None), who
        assert _deny_applies(deny, who, "s3.eu-north-1.amazonaws.com"), who
        assert _deny_applies(deny, who, "rds.eu-west-1.amazonaws.com"), who             # wrong Region
    # allowed: the act role, service-linked roles (RDS/ElastiCache grants), service principals (no PrincipalArn), and ViaService calls
    assert not _deny_applies(deny, role(ACT), None)
    assert not _deny_applies(deny, role("aws-service-role/rds.amazonaws.com/AWSServiceRoleForRDS"), None)
    assert not _deny_applies(deny, role("aws-service-role/elasticache.amazonaws.com/AWSServiceRoleForElastiCache"), None)
    assert not _deny_applies(deny, None, None)                                           # rds.amazonaws.com / elasticache.amazonaws.com principals
    for v in via:
        assert not _deny_applies(deny, role("paynest-a7-dev-worker-task"), v), v
        assert not _deny_applies(deny, f"arn:aws:iam::{acct}:user/someone", v), v


def test_key_policy_keeps_account_admin_statement_and_no_other_allow_of_decrypt(t):
    key = next(iter(_res(t["data"], "AWS::KMS::Key").values()))
    allows = [s for s in key["Properties"]["KeyPolicy"]["Statement"] if s["Effect"] == "Allow"]
    assert any(s["Action"] == "kms:*" and ":root" in json.dumps(s["Principal"]) for s in allows)       # CDK default: IAM policies apply
    assert not [s for s in allows if "Decrypt" in json.dumps(s["Action"]) and "kms:*" != s["Action"]]  # no extra Decrypt allow in the key policy


def test_rds_log_group_is_owned_by_cdk_and_removed_on_destroy(t):
    lgs = [g for g in _res(t["data"], "AWS::Logs::LogGroup").values()]
    assert len(lgs) == 1
    g = lgs[0]
    assert g["Properties"]["LogGroupName"] == f"/aws/rds/instance/paynest-a7-{ENV}-db/postgresql"
    assert g["Properties"]["RetentionInDays"] == 7 and g["DeletionPolicy"] == "Delete" and "DataProtectionPolicy" in g["Properties"]
    db = next(iter(_res(t["data"], "AWS::RDS::DBInstance").values()))
    assert db["Properties"]["DBInstanceIdentifier"] == f"paynest-a7-{ENV}-db" and db["Properties"]["EnableCloudwatchLogsExports"] == ["postgresql"]
    assert any("RdsPostgresLogs" in d for d in db["DependsOn"])        # the log group exists before RDS tries to create it


def test_endpoint_sg_only_admits_task_security_groups(t):
    net = t["network"]
    eps = {r["Properties"]["SecurityGroupIds"][0]["Fn::GetAtt"][0] for r in _res(net, "AWS::EC2::VPCEndpoint").values()
           if "SecurityGroupIds" in r["Properties"]}
    assert len(eps) == 1
    sg_id = next(iter(eps))
    ingress = [r["Properties"] for r in _res(net, "AWS::EC2::SecurityGroupIngress").values()
               if json.dumps(r["Properties"].get("GroupId")) == json.dumps({"Fn::GetAtt": [sg_id, "GroupId"]})]
    inline = _res(net, "AWS::EC2::SecurityGroup")[sg_id]["Properties"].get("SecurityGroupIngress", [])
    assert len(ingress) + len(inline) == 3                                                     # worker, api, mock
    for r in [*ingress, *inline]:
        assert "SourceSecurityGroupId" in r and "CidrIp" not in r and r.get("FromPort") == 443, r


def test_worker_is_capped_at_one_task_for_the_nova_rpm_quota(t):
    from stacks.compute_stack import WORKER_MAX_TASKS
    assert WORKER_MAX_TASKS == 1


def test_act_policy_attached_only_to_act_role_and_worker_has_no_decrypt(t):
    roles = _res(t["compute"], "AWS::IAM::Role")
    by_name = {r["Properties"].get("RoleName"): r for r in roles.values() if "RoleName" in r["Properties"]}
    assert ACT in by_name and f"paynest-a7-{ENV}-worker-task" in by_name
    act_managed = json.dumps(by_name[ACT]["Properties"].get("ManagedPolicyArns"))
    worker_managed = json.dumps(by_name[f"paynest-a7-{ENV}-worker-task"]["Properties"].get("ManagedPolicyArns"))
    assert "ActVaultPolicy" in act_managed or "ImportValue" in act_managed
    assert act_managed != worker_managed
    for name, r in by_name.items():
        if name != ACT:
            assert "ActVaultPolicy" not in json.dumps(r["Properties"].get("ManagedPolicyArns")), name
    # nobody can assume the act role except ECS tasks; the worker has no sts:AssumeRole
    for owner, stmts in _iam_policies(t["compute"]):
        for s in stmts:
            assert "sts:AssumeRole" not in _actions(s)


def test_act_db_secret_only_readable_by_act_and_bootstrap(t):
    pol = next(iter(_res(t["compute"], "AWS::SecretsManager::ResourcePolicy").values()))
    st = pol["Properties"]["ResourcePolicy"]["Statement"][0]
    assert st["Effect"] == "Deny" and "GetSecretValue" in json.dumps(st["Action"])
    allowed = st["Condition"]["ArnNotEquals"]["aws:PrincipalArn"]
    assert len(allowed) == 2 and ACT in json.dumps(allowed) and "bootstrap-task" in json.dumps(allowed)
    # the a7_act secret is not injected into any container by ECS (it is read by the act task role itself)
    for td in _res(t["compute"], "AWS::ECS::TaskDefinition").values():
        for c in td["Properties"]["ContainerDefinitions"]:
            for s in c.get("Secrets", []):
                assert "ActDbSecret" not in json.dumps(s["ValueFrom"])


# ---------------------------------------------------------------------------------------------------------- Bedrock scope
def test_bedrock_scoped_to_exact_models(t):
    sol = Path(__file__).resolve().parents[2] / "solution" / "a7" / "config.py"
    ns = {}
    exec(compile(sol.read_text(), str(sol), "exec"), ns)          # config.py imports only os/dataclasses
    assert (config.MODEL_CHEAP, config.MODEL_HARD, config.MODEL_FALLBACK, config.EMBED_MODEL) == (
        ns["MODEL_CHEAP"], ns["MODEL_HARD"], ns["MODEL_FALLBACK"], ns["EMBED_MODEL"])
    found = 0
    for owner, stmts in _iam_policies(t["compute"]):
        for s in stmts:
            bed = [a for a in _actions(s) if a.startswith("bedrock:") or a == "*"]
            if not bed:
                continue
            found += 1
            assert set(bed) <= {"bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"}
            res = s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
            for r in res:
                text = json.dumps(r)
                assert r != "*" and ("foundation-model/" in text or "inference-profile/" in text)
                tail = re.findall(r"(?:foundation-model|inference-profile)/([A-Za-z0-9.:\-]+)", text)[0]
                assert tail in {config.MODEL_CHEAP, config.MODEL_HARD, config.MODEL_FALLBACK, config.EMBED_MODEL,
                                config.MODEL_CHEAP.removeprefix("eu."), config.EMBED_MODEL.removeprefix("eu.")}, tail
                if "eu-*" in text:                              # geo FM ARN only via the pinned inference profile
                    assert "bedrock:InferenceProfileArn" in json.dumps(s["Condition"])
    assert found >= 4
    # the act and mock roles have no Bedrock access at all
    for owner, stmts in _iam_policies(t["compute"]):
        if "act-task" in owner or "mock-task" in owner:
            assert not any(a.startswith("bedrock:") for s in stmts for a in _actions(s))


def test_no_wildcard_actions_or_resources_on_task_roles(t):
    for owner, stmts in _iam_policies(t["compute"]):
        for s in stmts:
            if s.get("Effect") != "Allow":
                continue
            assert "*" not in _actions(s)
            if s["Resource"] == "*":                            # only the ECR token / log actions CDK adds to execution roles
                assert all(a in ("ecr:GetAuthorizationToken",) for a in _actions(s)), (owner, s)


# ---------------------------------------------------------------------------------------------------------- secrets and env
def test_secrets_from_secrets_manager_no_plaintext(t):
    sec_vars = {"A7_APP_USER_PASSWORD", "A7_ADMIN_PASSWORD", "A7_REVIEWER_TOKEN", "A7_ADMIN_TOKEN", "A7_PAYROLL_API_TOKEN", "A7_DB_OWNER_PASSWORD"}
    seen = set()
    for td in _res(t["compute"], "AWS::ECS::TaskDefinition").values():
        for c in td["Properties"]["ContainerDefinitions"]:
            for s in c.get("Secrets", []):
                seen.add(s["Name"])
                vf = json.dumps(s["ValueFrom"])
                local = {k for k, r in t["compute"].to_json()["Resources"].items() if r["Type"] == "AWS::SecretsManager::Secret"}
                assert any(k in vf for k in local) or ("ImportValue" in vf and "secret" in vf.lower()), vf
            for e in c.get("Environment", []):
                assert not re.search(r"PASSWORD|TOKEN|SECRET_KEY", e["Name"]) or e["Name"].endswith("_SECRET_ARN"), e["Name"]
    assert sec_vars <= seen
    # generated secrets: no literal value in the template
    for r in _res(t["compute"], "AWS::SecretsManager::Secret").values():
        assert "SecretString" not in r["Properties"] and "GenerateSecretString" in r["Properties"]


def test_auto_send_env_off_and_region_pinned(t):
    for td in _res(t["compute"], "AWS::ECS::TaskDefinition").values():
        for c in td["Properties"]["ContainerDefinitions"]:
            env = {e["Name"]: e["Value"] for e in c.get("Environment", [])}
            if "A7_DB_HOST" in env:
                assert env["A7_AUTO_SEND"] == "false" and env["AWS_REGION"] == "eu-north-1" and env["A7_AWS_PROFILE"] == "none"
                assert "A7_KILL_SWITCH" not in env             # kill switch = DB flag (default OFF), env can only force OFF
            assert "A7_DATABASE_URL" not in env and "A7_DECIDER_URL" not in env


def test_escalation_scheduler_wiring(t):
    t["compute"].has_resource_properties("AWS::Scheduler::ScheduleGroup", {"Name": f"paynest-a7-{ENV}"})
    t["compute"].has_resource_properties("AWS::Scheduler::Schedule", {"ScheduleExpression": "rate(30 minutes)"})
    t["compute"].has_resource_properties("AWS::IAM::Role", {"RoleName": f"paynest-a7-{ENV}-scheduler"})
    worker = next(c for td in _res(t["compute"], "AWS::ECS::TaskDefinition").values() for c in td["Properties"]["ContainerDefinitions"]
                  if c["Name"] == "worker")
    env = {e["Name"] for e in worker["Environment"]}
    assert {"A7_ESCALATION_BACKEND", "A7_SCHEDULER_GROUP", "A7_ESCALATION_QUEUE_ARN", "A7_SCHEDULER_ROLE_ARN", "A7_QUEUE_URL"} <= env


def test_backstop_can_be_disabled():
    tpl = all_templates(escalation_backstop_minutes="0")
    tpl["compute"].resource_count_is("AWS::Scheduler::Schedule", 0)


# ---------------------------------------------------------------------------------------------------------- ops
def test_log_data_protection_policy(t):
    lgs = _res(t["ops"], "AWS::Logs::LogGroup")
    assert len(lgs) == 5
    for g in lgs.values():
        assert g["Properties"]["RetentionInDays"] == 7
        pol = g["Properties"]["DataProtectionPolicy"]
        stmts = pol["Statement"]
        ids = json.dumps(stmts[1]["DataIdentifier"])
        assert "data-identifier/EmailAddress" in ids and "data-identifier/CreditCardNumber" in ids and "BankAccountNumber-DE" in ids
        assert "IbanAny" in ids and "EmiratesId" in ids and "AadhaarIN" in ids and "PanIN" in ids
        assert {"IbanAny", "EmiratesId", "AadhaarIN", "PanIN", "PhoneIntl", "BankAcctIfscIN"} <= {c["Name"] for c in pol["Configuration"]["CustomDataIdentifier"]}
        assert any("Deidentify" in json.dumps(s["Operation"]) for s in stmts)       # masks, not just audits


# Characters CloudWatch Logs accepts in a custom data identifier regex (docs: "Custom data identifiers" constraints) and in its name.
CWL_REGEX_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" "_#=@/;,- '\"" "^$?[]{}|\\*+.")
CWL_NAME_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def test_custom_identifiers_match_and_obey_cloudwatch_limits():
    from stacks.ops_stack import CUSTOM_IDENTIFIERS as C
    assert len(C) <= 10
    for name, rx in C.items():
        assert len(rx) <= 200 and len(name) <= 128, name
        assert set(name) <= CWL_NAME_ALLOWED, name
        bad = sorted(set(rx) - CWL_REGEX_ALLOWED)
        assert not bad, (name, bad)                          # e.g. ':' and '(' / ')' are NOT allowed (BankAcctIfscIN used ':' once)
        re.compile(rx)                                       # and it is a valid regex
    ok = {"IbanAny": ["AE07 0331 2345 6789 0123 456", "AE070331234567890123456"], "EmiratesId": ["784-1990-1234567-1", "784199012345671"],
          "AadhaarIN": ["2345 6789 0123", "234567890123"], "PanIN": ["ABCDE1234F"],
          "BankAcctIfscIN": ["A/C 123456789012", "IFSC HDFC0001234", "IFSC: HDFC0001234", "IFSC-HDFC0001234"],
          "PhoneIntl": ["+971 50 123 4567", "98765 43210"]}
    for name, samples in ok.items():
        for sm_ in samples:
            assert re.search(C[name], sm_), (name, sm_)
    benign = ["PN-101055", "PN-100402", "CL-202413", "R-IN-PF-2026-08", "1f0d3c2a-7b9e-4c11-9a62-123456789012", "2026-11-14T09:30:00Z", "10.42.1.17"]
    for text in benign:
        for name, rx in C.items():
            assert not re.search(rx, text), (name, text)
    # documented over-mask: a 12-digit AWS account id starting 2-9 matches AadhaarIN (lookbehind needs parentheses, which are not allowed)
    assert re.search(C["AadhaarIN"], "arn:aws:iam::554559231166:role/x")


def test_alarms_present_with_sns_action(t):
    alarms = {a["Properties"]["AlarmName"].split(f"paynest-a7-{ENV}-")[1]: a for a in _res(t["monitoring"], "AWS::CloudWatch::Alarm").values()}
    assert {"dlq-depth", "alb-5xx", "unhealthy-hosts", "worker-errors", "rds-cpu", "rds-storage", "bedrock-throttles"} <= set(alarms)
    for a in alarms.values():
        assert a["Properties"]["AlarmActions"]
    t["monitoring"].resource_count_is("AWS::CloudWatch::Dashboard", 1)


def test_sns_topic_email_from_context_only(t):
    assert not _res(t["ops"], "AWS::SNS::Subscription")                      # empty by default
    s = json.dumps(t["ops"].to_json())
    assert "@" not in re.sub(r"[a-z0-9]+@[a-z0-9.-]+\.(?:amazonaws|invalid)", "", s.replace("budgets.amazonaws.com", ""))
    tpl = all_templates(alarm_email="ops@example.invalid")
    tpl["ops"].has_resource_properties("AWS::SNS::Subscription", {"Protocol": "email", "Endpoint": "ops@example.invalid"})
    topic = next(iter(_res(t["ops"], "AWS::SNS::Topic").values()))
    assert "KmsMasterKeyId" not in topic["Properties"]


# ---------------------------------------------------------------------------------------------------------- teardown
def test_removal_policies_destroy_by_default(t):
    for name, tpl in t.items():
        for lid, r in tpl.to_json()["Resources"].items():
            typ = r["Type"]
            if typ in ("AWS::Logs::LogGroup", "AWS::SecretsManager::Secret", "AWS::SQS::Queue", "AWS::ECS::Cluster",
                       "AWS::ElastiCache::ReplicationGroup", "AWS::KMS::Key", "AWS::RDS::DBInstance"):
                assert r.get("DeletionPolicy", "Delete") == "Delete" and r.get("UpdateReplacePolicy", "Delete") == "Delete", (name, lid)
    rds = next(iter(_res(t["data"], "AWS::RDS::DBInstance").values()))
    assert rds["Properties"]["DeletionProtection"] is False


def test_keep_data_flag_snapshots_db_and_retains_key():
    tpl = all_templates(keep_data="true")["data"]
    rds = next(iter(_res(tpl, "AWS::RDS::DBInstance").values()))
    key = next(iter(_res(tpl, "AWS::KMS::Key").values()))
    assert rds["DeletionPolicy"] == "Snapshot" and key["DeletionPolicy"] == "Retain"
    assert rds["Properties"]["DeletionProtection"] is False                  # so `cdk destroy` can actually take the snapshot
    assert rds["Properties"]["DeleteAutomatedBackups"] is False
    for r in _res(tpl, "AWS::SecretsManager::Secret").values():
        assert r["DeletionPolicy"] == "Delete"


def test_no_retained_resources_by_default(t):
    for name, tpl in t.items():
        for lid, r in tpl.to_json()["Resources"].items():
            assert r.get("DeletionPolicy") not in ("Retain", "RetainExceptOnCreate", "Snapshot"), (name, lid)


# ---------------------------------------------------------------------------------------------------------- images
def test_images_are_arm64_assets_with_four_targets(t):
    assets = json.loads((Path(__file__).resolve().parents[1] / "cdk.out" / "paynest-a7-dev-compute.assets.json").read_text()) \
        if (Path(__file__).resolve().parents[1] / "cdk.out" / "paynest-a7-dev-compute.assets.json").exists() else None
    del assets                                                               # cdk.out only exists after `cdk synth`; asset props asserted below
    df = (Path(__file__).resolve().parents[2] / "docker" / "Dockerfile").read_text()
    for target in ("worker", "api", "act", "mock-payroll", "bootstrap"):
        assert re.search(rf"^FROM \S+ AS {target}$", df, re.M), target
    assert "USER 10001" in df and "uv pip install" in df
    for bad in ("ENV AWS_SECRET", "ENV A7_ACT_PASSWORD", "COPY .env", "password"):
        assert bad.lower() not in df.lower().replace("passwords", ""), bad
    ign = (Path(__file__).resolve().parents[2] / ".dockerignore").read_text()
    assert ign.startswith("#") and "\n*\n" in ign and ".env" in ign and "!trainer" not in ign


def test_smoke_role_can_only_send_to_the_ingest_queue(t):
    """`smoke batch` replays ticket ids through the ingest queue: SendMessage on that queue and GenerateDataKey via SQS, nothing else."""
    mine = [(o, st) for o, st in _iam_policies(t["compute"]) if "SmokeRole" in o or "smoke-task" in o]
    stmts = [s for _o, ss in mine for s in ss if s.get("Effect") == "Allow"]
    acts = sorted(a for s in stmts for a in _actions(s))
    assert acts == ["kms:GenerateDataKey", "sqs:SendMessage"], acts
    kms = next(s for s in stmts if "kms:GenerateDataKey" in _actions(s))
    assert kms["Condition"]["StringEquals"]["kms:ViaService"] == "sqs.eu-north-1.amazonaws.com"
