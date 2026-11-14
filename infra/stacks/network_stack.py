"""NetworkStack: VPC (no NAT), 9 VPC endpoints, least-privilege security groups."""
from aws_cdk import CfnOutput, Stack, Tags
from aws_cdk import aws_ec2 as ec2
from constructs import Construct

from config import Sizing

# Interface endpoints (8). The S3 gateway endpoint is the 9th.
INTERFACE_ENDPOINTS = {
    "bedrock-runtime": ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
    "sqs": ec2.InterfaceVpcEndpointAwsService.SQS,
    "kms": ec2.InterfaceVpcEndpointAwsService.KMS,
    "secretsmanager": ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
    "ecr-api": ec2.InterfaceVpcEndpointAwsService.ECR,
    "ecr-dkr": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
    "logs": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
    # Verified present in eu-north-1 (DescribeVpcEndpointServices, 2026-11-09).
    "scheduler": ec2.InterfaceVpcEndpointAwsService("scheduler"),
}

API_PORT = 8080
MOCK_PORT = 8080          # mock-payroll container port
MOCK_LISTENER_PORT = 8081  # internal ALB listener that fronts it (worker -> ALB:8081 -> mock)


class NetworkStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, sizing: Sizing, public_alb: bool, **kw):
        super().__init__(scope, cid, **kw)

        # Decision: ALB is INTERNAL by default (a public ALB needs WAF + ACM cert, out of scope).
        # public_alb=true adds public subnets (ALB only) and a 443-from-anywhere ALB SG.
        subnets = [ec2.SubnetConfiguration(
            name="workload", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24)]
        if public_alb:
            subnets.append(ec2.SubnetConfiguration(
                name="alb", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=26, map_public_ip_on_launch=False))

        self.vpc = ec2.Vpc(
            # AZ list is pre-seeded in app.py context, so synth needs no AWS credentials.
            self, "Vpc", max_azs=2, nat_gateways=0, ip_addresses=ec2.IpAddresses.cidr("10.42.0.0/16"),
            subnet_configuration=subnets, enable_dns_hostnames=True, enable_dns_support=True,
        )
        self.public_alb = public_alb
        self.workload_subnets = ec2.SubnetSelection(subnet_group_name="workload")
        self.alb_subnets = ec2.SubnetSelection(subnet_group_name="alb" if public_alb else "workload")

        # ---- security groups (all here so Data/Compute stacks need no cyclic cross-stack rules)
        def sg(name: str, desc: str, outbound: bool) -> ec2.SecurityGroup:
            return ec2.SecurityGroup(self, name, vpc=self.vpc, description=desc, allow_all_outbound=outbound)

        # Workloads sit in isolated subnets (no route out), so open egress only reaches the VPC/endpoints.
        self.alb_sg = sg("AlbSg", "PayNest A7 ALB", False)
        self.api_sg = sg("ApiSg", "PayNest A7 reviewer API", True)
        self.worker_sg = sg("WorkerSg", "PayNest A7 SQS worker", True)
        self.mock_sg = sg("MockSg", "PayNest A7 mock payroll API (demo)", True)
        self.db_sg = sg("DbSg", "PayNest A7 RDS PostgreSQL", False)
        self.cache_sg = sg("CacheSg", "PayNest A7 Valkey", False)
        self.endpoint_sg = sg("EndpointSg", "PayNest A7 interface endpoints", False)

        if public_alb:
            self.alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS from internet (public_alb=true)")
        else:
            self.alb_sg.add_ingress_rule(ec2.Peer.ipv4(self.vpc.vpc_cidr_block), ec2.Port.tcp(80), "HTTP from inside the VPC")
        self.alb_sg.add_egress_rule(self.api_sg, ec2.Port.tcp(API_PORT), "ALB to API tasks")
        self.alb_sg.add_egress_rule(self.mock_sg, ec2.Port.tcp(MOCK_PORT), "ALB to mock payroll tasks")
        self.alb_sg.add_ingress_rule(self.worker_sg, ec2.Port.tcp(MOCK_LISTENER_PORT), "Worker to mock payroll via ALB")
        self.mock_sg.add_ingress_rule(self.alb_sg, ec2.Port.tcp(MOCK_PORT), "From ALB only")
        self.api_sg.add_ingress_rule(self.alb_sg, ec2.Port.tcp(API_PORT), "From ALB only")

        for src, nm in ((self.worker_sg, "worker"), (self.api_sg, "api")):
            self.db_sg.add_ingress_rule(src, ec2.Port.tcp(5432), f"PostgreSQL from {nm}")
            self.cache_sg.add_ingress_rule(src, ec2.Port.tcp(6379), f"Valkey TLS from {nm}")
        for src, nm in ((self.worker_sg, "worker"), (self.api_sg, "api"), (self.mock_sg, "mock")):
            self.endpoint_sg.add_ingress_rule(src, ec2.Port.tcp(443), f"HTTPS from {nm}")

        # ---- endpoints: S3 gateway + 8 interface endpoints
        self.vpc.add_gateway_endpoint(
            "S3", service=ec2.GatewayVpcEndpointAwsService.S3, subnets=[self.workload_subnets])
        isolated = self.vpc.select_subnets(subnet_group_name="workload").subnets
        ep_subnets = ec2.SubnetSelection(subnets=isolated[: sizing.endpoint_azs])
        self.endpoints = {}
        for name, svc in INTERFACE_ENDPOINTS.items():
            self.endpoints[name] = self.vpc.add_interface_endpoint(
                f"Ep-{name}", service=svc, subnets=ep_subnets, security_groups=[self.endpoint_sg],
                private_dns_enabled=True, open=False)      # open=False: no automatic 443-from-VPC-CIDR rule; only the task SGs above

        CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
        Tags.of(self).add("stack", "network")
