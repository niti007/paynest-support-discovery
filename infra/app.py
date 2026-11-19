#!/usr/bin/env python3
"""PayNest A7 CDK app. Context: region, env_name, sizing=dev|doc, public_alb, account (from env)."""
import os

import aws_cdk as cdk

from config import PROJECT_TAGS, REGION, SIZINGS
from stacks.compute_stack import ComputeStack
from stacks.data_stack import DataStack
from stacks.monitoring_stack import MonitoringStack
from stacks.network_stack import NetworkStack
from stacks.ops_stack import OpsStack


def _flag(app, name: str) -> bool:
    return str(app.node.try_get_context(name)).lower() == "true"


def build_app(**ctx) -> cdk.App:
    app = cdk.App(context=ctx or None)
    region = app.node.try_get_context("region") or REGION
    if region != REGION:
        raise ValueError(f"only {REGION} is allowed in this project (got {region})")
    env_name = app.node.try_get_context("env_name") or "dev"
    sizing_name = app.node.try_get_context("sizing") or "dev"
    if sizing_name not in SIZINGS:
        raise ValueError(f"sizing must be one of {list(SIZINGS)}")
    sizing = SIZINGS[sizing_name]
    public_alb = str(app.node.try_get_context("public_alb")).lower() == "true"
    # Account comes from the CLI environment, never hard-coded. Falls back to a dummy for offline synth/tests.
    account = os.environ.get("CDK_DEFAULT_ACCOUNT") or "000000000000"
    env = cdk.Environment(account=account, region=region)
    # Pre-seed the AZ lookup so synth never calls AWS (eu-north-1 has a/b/c; we use a and b).
    app.node.set_context(f"availability-zones:account={account}:region={region}", [f"{region}a", f"{region}b"])

    net = NetworkStack(app, f"paynest-a7-{env_name}-network", sizing=sizing, public_alb=public_alb, env=env)
    data = DataStack(app, f"paynest-a7-{env_name}-data", sizing=sizing, env_name=env_name,
                     vpc=net.vpc, db_sg=net.db_sg, cache_sg=net.cache_sg,
                     keep_data=_flag(app, "keep_data"), env=env)
    # Context (all optional): alarm_email (EMPTY by default, never hard-coded), budget_usd (default 20), log_retention_days (7|14|30),
    # keep_data (true = snapshot RDS + retain the CMK on destroy), alb_certificate_arn (public_alb only), escalation_backstop_minutes.
    def ctx(name, default):
        v = app.node.try_get_context(name)
        return default if v in (None, "") else v

    ops = OpsStack(app, f"paynest-a7-{env_name}-ops", env_name=env_name, retention_days=int(ctx("log_retention_days", 7)),
                   alarm_email=str(ctx("alarm_email", "")), budget_usd=float(ctx("budget_usd", 20)), env=env)
    compute = ComputeStack(app, f"paynest-a7-{env_name}-compute", sizing=sizing, env_name=env_name, net=net, data=data, ops=ops,
                           certificate_arn=str(ctx("alb_certificate_arn", "")),
                           escalation_backstop_minutes=int(ctx("escalation_backstop_minutes", 30)), env=env)
    monitoring = MonitoringStack(app, f"paynest-a7-{env_name}-monitoring", env_name=env_name, data=data, compute=compute, ops=ops, env=env)
    for k, v in PROJECT_TAGS.items():
        cdk.Tags.of(app).add(k, v)

    if app.node.try_get_context("nag") in (True, "true"):
        import cdk_nag
        cdk.Aspects.of(app).add(cdk_nag.AwsSolutionsChecks(verbose=True))
        _suppress(net, data, sizing)
        _suppress_p6(ops, compute, monitoring)
    return app


def _suppress(net, data, sizing):
    """cdk-nag (AwsSolutions) suppressions. Every one carries a written reason."""
    from cdk_nag import NagPackSuppression as S, NagSuppressions as N
    N.add_stack_suppressions(net, [
        S(id="AwsSolutions-VPC7", reason="VPC Flow Logs cost CloudWatch ingestion on a USD-credit demo; no NAT/IGW "
                                          "in the private design. Enable for a production deployment."),
        S(id="CdkNagValidationFailure", reason="Rule cannot evaluate the VPC CIDR token (SG ingress from the VPC "
                                                "CIDR, not 0.0.0.0/0); the unit tests assert the ingress instead."),
    ])
    rules = [
        S(id="AwsSolutions-RDS3", reason="Single-AZ by design (cost; Session 4 sizing is single-AZ). Training demo."),
        S(id="AwsSolutions-RDS11", reason="Default port 5432 kept: clients/schema/probes assume it; access is "
                                          "restricted to worker/api SGs in isolated subnets with no internet route."),
        S(id="AwsSolutions-SMG4", reason="app_user secret is a dev/demo credential reset by the bootstrap task; "
                                         "rotation needs a rotation Lambda (out of scope). Master secret is RDS-managed."),
        S(id="AwsSolutions-AEC4", reason="Single-node Valkey is a cache only (loss = cache miss); Session 4 sizing."),
        S(id="AwsSolutions-AEC6", reason="Valkey is TLS-only, in isolated subnets, reachable only from worker/api "
                                         "SGs. AUTH/RBAC is a follow-up; the cache holds no PII (masked text only)."),
    ]
    if sizing.destroyable:
        rules.append(S(id="AwsSolutions-RDS10", reason="dev sizing is intentionally destroyable (cdk destroy). "
                                                       "The doc sizing enables deletion protection."))
    N.add_stack_suppressions(data, rules)


def _suppress_p6(ops, compute, monitoring):
    """cdk-nag suppressions for OpsStack / ComputeStack / MonitoringStack. Every one carries a written reason."""
    from cdk_nag import NagPackSuppression as S, NagSuppressions as N
    N.add_stack_suppressions(compute, [
        S(id="AwsSolutions-SMG4", reason="Demo credentials generated ONCE at stack creation (never in the template); rotation needs a rotation Lambda (out of scope). Bootstrap sets the DB passwords from them; destroy removes them."),
        S(id="AwsSolutions-ECS4", reason="Container Insights bills per metric/log volume; the demo uses the ALB/SQS/ECS alarms and "
                                         "dashboard in MonitoringStack instead."),
        S(id="AwsSolutions-ELB2", reason="ALB access logs need an S3 bucket (cost, extra resource to destroy). App logs go to CloudWatch."),
        S(id="AwsSolutions-ECS2", reason="Environment holds only non-secret settings (hostnames, ARNs, flags); every secret is an ECS secret."),
    ])
    # IAM5 is suppressed per ROLE and per RESOURCE (applies_to), never stack-wide, so any new wildcard still fails the nag run.
    # (Stale suppressions removed after a per-suppression probe: IAM4, monitoring IAM5, ops SNS2 and compute EC23 never fire. The network
    # CdkNagValidationFailure (EC23 on the VPC CIDR token) does fire, so it stays.)
    # The internal ALB listens on plain HTTP (no nag rule fires): accepted demo risk, bearer tokens and masked ticket text stay inside the VPC
    # (isolated subnets, no IGW/NAT, SG-restricted); a public ALB requires an ACM certificate and HTTPS (enforced in ComputeStack).
    base = compute.node.path
    bedrock_why = {"amazon.nova-lite-v1:0": "EU geo inference profile (Nova Lite)", "cohere.embed-v4:0": "EU geo inference profile (Cohere embeddings)"}
    for role, models in (("WorkerRole", bedrock_why), ("ApiRole", {"cohere.embed-v4:0": bedrock_why["cohere.embed-v4:0"]}),
                         ("BootstrapRole", {"cohere.embed-v4:0": bedrock_why["cohere.embed-v4:0"]})):
        N.add_resource_suppressions_by_path(compute, f"/{base}/{role}/DefaultPolicy/Resource", [
            S(id="AwsSolutions-IAM5", applies_to=[f"Resource::arn:aws:bedrock:eu-*::foundation-model/{m}"],
              reason=f"{why}: the underlying foundation model is served from several EU Regions, so the Region is wildcarded to eu-* "
                     "and the statement is conditioned on bedrock:InferenceProfileArn (calls must come through that profile).")
            for m, why in models.items()])
    for role in ("WorkerRole", "ApiRole"):
        N.add_resource_suppressions_by_path(compute, f"/{base}/{role}/DefaultPolicy/Resource", [
            S(id="AwsSolutions-IAM5", applies_to=[{"regex": "/^Resource::arn:aws:scheduler:[a-z0-9-]+:[0-9]+:schedule\\/paynest-a7-[a-z0-9-]+\\/\\*$/"}],
              reason="One-time escalation schedules get a generated name (a7-esc-<id>) inside the single paynest-a7-<env> schedule group; "
                     "CreateSchedule cannot be scoped tighter than the group.")])
    for task in ("ApiTask", "MockTask", "WorkerTask", "ActTask", "SmokeTask", "BootstrapTask"):
        N.add_resource_suppressions_by_path(compute, f"/{base}/{task}/ExecutionRole/DefaultPolicy/Resource", [
            S(id="AwsSolutions-IAM5", applies_to=["Resource::*"],
              reason="ecr:GetAuthorizationToken does not support resource-level permissions (AWS requires Resource *); it is the only wildcard here.")])


if __name__ == "__main__":
    build_app().synth()
