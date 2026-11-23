"""Assertions on the synthesized NetworkStack / DataStack (no AWS calls)."""
import json
import os

import pytest
from aws_cdk.assertions import Match, Template

os.environ.setdefault("JSII_SILENCE_WARNING_END_OF_LIFE_NODE_VERSION", "1")
from app import build_app  # noqa: E402


def synth(sizing="dev", public_alb="false"):
    app = build_app(region="eu-north-1", env_name="dev", sizing=sizing, public_alb=public_alb,
                    alb_certificate_arn="arn:aws:acm:eu-north-1:000000000000:certificate/test")
    net = app.node.find_child("paynest-a7-dev-network")
    data = app.node.find_child("paynest-a7-dev-data")
    return Template.from_stack(net), Template.from_stack(data), net, data


@pytest.fixture(scope="module")
def dev():
    return synth("dev")


@pytest.fixture(scope="module")
def doc():
    return synth("doc")


@pytest.fixture(scope="module")
def pub():
    return synth("dev", "true")


def test_no_nat_gateway(dev, doc, pub):
    for t in (dev[0], doc[0], pub[0]):
        t.resource_count_is("AWS::EC2::NatGateway", 0)
        t.resource_count_is("AWS::EC2::EIP", 0)


def test_all_nine_endpoints(dev):
    t = dev[0]
    eps = t.find_resources("AWS::EC2::VPCEndpoint")
    assert len(eps) == 9
    services = set()
    for r in eps.values():
        sn = r["Properties"]["ServiceName"]
        services.add(sn if isinstance(sn, str) else json.dumps(sn))
    for svc in ["bedrock-runtime", "sqs", "kms", "secretsmanager", "ecr.api", "ecr.dkr", "logs", "scheduler", "s3"]:
        assert any(svc in s and "eu-north-1" in s or (svc in s) for s in services), svc
    t.resource_count_is("AWS::EC2::VPCEndpoint", 9)
    gw = [r for r in eps.values() if r["Properties"].get("VpcEndpointType") == "Gateway"]
    assert len(gw) == 1


@pytest.mark.parametrize("fixture,azs", [("dev", 1), ("doc", 2)])
def test_endpoint_az_count(request, fixture, azs):
    t = request.getfixturevalue(fixture)[0]
    for r in t.find_resources("AWS::EC2::VPCEndpoint").values():
        if r["Properties"].get("VpcEndpointType") == "Interface":
            assert len(r["Properties"]["SubnetIds"]) == azs


def test_rds_private_encrypted_sized(dev, doc):
    t = dev[1]
    t.has_resource_properties("AWS::RDS::DBInstance", {
        "DBInstanceClass": "db.t4g.micro", "Engine": "postgres", "EngineVersion": "17.11",
        "PubliclyAccessible": False, "StorageEncrypted": True,
        "KmsKeyId": Match.any_value(), "ManageMasterUserPassword": True,
        "BackupRetentionPeriod": 1, "DeletionProtection": False, "DeleteAutomatedBackups": True,
    })
    rds = next(iter(t.find_resources("AWS::RDS::DBInstance").values()))
    assert rds["DeletionPolicy"] == "Delete"
    # the RDS key is the PII vault key (the one with the Decrypt-deny policy)
    keys = t.find_resources("AWS::KMS::Key")
    assert len(keys) == 1
    assert "DataKey" in json.dumps(rds["Properties"]["KmsKeyId"])
    doc[1].has_resource_properties("AWS::RDS::DBInstance", {
        "DBInstanceClass": "db.t4g.medium", "DeletionProtection": True, "BackupRetentionPeriod": 7})
    drds = next(iter(doc[1].find_resources("AWS::RDS::DBInstance").values()))
    assert drds["DeletionPolicy"] == "Snapshot"


def test_rds_in_isolated_subnets(dev):
    dev[1].resource_count_is("AWS::RDS::DBSubnetGroup", 1)


def test_kms_vault_policy(dev):
    t = dev[1]
    t.has_resource_properties("AWS::KMS::Key", {"EnableKeyRotation": True})
    key = next(iter(t.find_resources("AWS::KMS::Key").values()))
    stmts = key["Properties"]["KeyPolicy"]["Statement"]
    deny = [s for s in stmts if s["Effect"] == "Deny"]
    assert deny and deny[0]["Action"] == "kms:Decrypt"
    # worker policy: GenerateDataKey only
    t.has_resource_properties("AWS::IAM::ManagedPolicy", {
        "PolicyDocument": {"Statement": [Match.object_like({"Action": "kms:GenerateDataKey"})]}})
    for p in t.find_resources("AWS::IAM::ManagedPolicy").values():
        s = json.dumps(p["Properties"]["PolicyDocument"])
        if "worker" in json.dumps(p["Properties"]["ManagedPolicyName"]):
            assert "Decrypt" not in s


def test_valkey(dev, doc):
    dev[1].has_resource_properties("AWS::ElastiCache::ReplicationGroup", {
        "Engine": "valkey", "EngineVersion": "9.0", "CacheNodeType": "cache.t4g.micro",
        "NumCacheClusters": 1, "TransitEncryptionEnabled": True, "AtRestEncryptionEnabled": True})
    doc[1].has_resource_properties("AWS::ElastiCache::ReplicationGroup", {"CacheNodeType": "cache.t4g.small"})


def test_sqs_encrypted_with_dlq(dev):
    t = dev[1]
    queues = t.find_resources("AWS::SQS::Queue")
    assert len(queues) == 2
    for q in queues.values():
        assert "KmsMasterKeyId" in q["Properties"]
    with_dlq = [q for q in queues.values() if "RedrivePolicy" in q["Properties"]]
    assert len(with_dlq) == 1
    assert with_dlq[0]["Properties"]["RedrivePolicy"]["maxReceiveCount"] == 5


def _open_ingress(template):
    """Return (sg logical id, rule) for each ingress rule from 0.0.0.0/0."""
    out = []
    for lid, r in template.find_resources("AWS::EC2::SecurityGroup").items():
        for rule in r["Properties"].get("SecurityGroupIngress", []):
            if rule.get("CidrIp") == "0.0.0.0/0":
                out.append((lid, rule))
    for lid, r in template.find_resources("AWS::EC2::SecurityGroupIngress").items():
        if r["Properties"].get("CidrIp") == "0.0.0.0/0":
            out.append((lid, r["Properties"]))
    return out


def test_no_open_ingress_by_default(dev, doc):
    assert _open_ingress(dev[0]) == []
    assert _open_ingress(doc[0]) == []


def test_open_ingress_only_alb_when_public(pub):
    rules = _open_ingress(pub[0])
    assert len(rules) == 1
    assert rules[0][0].startswith("AlbSg") and rules[0][1]["FromPort"] == 443
    # public subnets exist only in the public-ALB variant, still no NAT
    pub[0].resource_count_is("AWS::EC2::NatGateway", 0)


def test_region_and_account(dev, doc, pub):
    for t, net, data in ((dev[0], dev[2], dev[3]), (doc[0], doc[2], doc[3])):
        assert net.region == "eu-north-1" and data.region == "eu-north-1"
    # no hard-coded region other than eu-north-1 in any template
    for tpl in (dev[0], dev[1], doc[0], doc[1]):
        s = json.dumps(tpl.to_json())
        for other in ("us-east-1", "eu-west-1", "ap-south-1", "me-central-1"):
            assert other not in s


def test_rejects_other_region():
    with pytest.raises(Exception):
        build_app(region="us-east-1")


def test_tags(dev):
    for tpl in (dev[0], dev[1]):
        for r in tpl.find_resources("AWS::EC2::SecurityGroup").values():
            tags = {t["Key"]: t["Value"] for t in r["Properties"]["Tags"]}
            assert tags["project"] == "paynest" and tags["app"] == "a7"
    for r in dev[1].find_resources("AWS::RDS::DBInstance").values():
        tags = {t["Key"]: t["Value"] for t in r["Properties"]["Tags"]}
        assert tags["project"] == "paynest" and tags["app"] == "a7"
