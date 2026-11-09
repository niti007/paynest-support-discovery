#!/usr/bin/env python3
"""PayNest A7 CDK app. Context: region, env_name, sizing=dev|doc, public_alb, account (from env)."""
import os

import aws_cdk as cdk

from config import PROJECT_TAGS, REGION, SIZINGS
from stacks.data_stack import DataStack
from stacks.network_stack import NetworkStack


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
                     vpc=net.vpc, db_sg=net.db_sg, cache_sg=net.cache_sg, env=env)
    for k, v in PROJECT_TAGS.items():
        cdk.Tags.of(app).add(k, v)

    if app.node.try_get_context("nag") in (True, "true"):
        import cdk_nag
        cdk.Aspects.of(app).add(cdk_nag.AwsSolutionsChecks(verbose=True))
        _suppress(net, data, sizing)
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


if __name__ == "__main__":
    build_app().synth()
