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
    N.add_stack_suppressions(ops, [
        S(id="AwsSolutions-SNS2", reason="Alarm/budget payloads carry no PII. SSE with the AWS-managed key would stop CloudWatch "
                                         "and Budgets from publishing; TLS is enforced by the topic policy."),
    ])
    N.add_stack_suppressions(compute, [
        S(id="AwsSolutions-SMG4", reason="Demo credentials, regenerated on every deploy; rotation needs a rotation Lambda (out of scope)."),
        S(id="AwsSolutions-ECS4", reason="Container Insights bills per metric/log volume; the demo uses the ALB/SQS/ECS alarms and "
                                         "dashboard in MonitoringStack instead."),
        S(id="AwsSolutions-ELB2", reason="ALB access logs need an S3 bucket (cost, extra resource to destroy). App logs go to CloudWatch."),
        S(id="AwsSolutions-ECS2", reason="Environment holds only non-secret settings (hostnames, ARNs, flags); every secret is an ECS secret."),
        S(id="AwsSolutions-EC23", reason="False positive: the ALB SG allows ingress only from the VPC CIDR (internal ALB) or, when "
                                         "public_alb=true, 443 only; the unit tests assert both."),
        S(id="AwsSolutions-IAM5", reason="Wildcards are limited to: EU-region prefix of the Bedrock foundation-model ARN (geo inference "
                                         "profile, pinned by bedrock:InferenceProfileArn); scheduler schedule/<group>/*; Secrets Manager "
                                         "random ARN suffix; ECR/logs resource policies CDK generates for the execution roles."),
        S(id="AwsSolutions-IAM4", reason="AWS-managed policies only on the CDK-created execution roles (ECS task execution policy)."),
    ])
    N.add_stack_suppressions(monitoring, [
        S(id="AwsSolutions-IAM5", reason="CDK-generated metric-filter/dashboard permissions use resource wildcards."),
    ])


if __name__ == "__main__":
    build_app().synth()
