"""ComputeStack: ECS Fargate (ARM64) cluster, internal ALB, api / worker / mock-payroll services, bootstrap + act RunTask definitions.

Everything runs in the private isolated `workload` subnets (no NAT, no public IPs) and reaches AWS through the NetworkStack
endpoints (ecr.api, ecr.dkr, s3 gateway, logs, sqs, kms, secretsmanager, bedrock-runtime, scheduler).

ACT DESIGN. The graph's `act` node only writes outbox rows (app_user, no decryption). The only code that opens the PII vault is
`act.render_outbound` (send-time restore). It runs as its OWN task definition (`act`, RunTask, no service, zero idle cost) under the
role `paynest-a7-<env>-act-task`, which alone holds kms:Decrypt on the vault key and (with the bootstrap task, which sets the
password once) GetSecretValue on the a7_act DB secret. The worker never assumes that role (it has no sts:AssumeRole), so a worker
compromise yields GenerateDataKey (write-only) but cannot read a single vault value. The a7_act secret is NOT injected by ECS (that
would make the execution role a reader); the act container reads it with its own task role (A7_ACT_SECRET_ARN).

IAM notes: ECS secret injection is done by each task's EXECUTION role, so each task definition has its own execution role that can
read only that task's secrets. SQS SSE-KMS consumers need kms:Decrypt; the worker's grant is conditioned on kms:ViaService=sqs, and the
key policy denies every non-service Decrypt except the act role.
"""
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_applicationautoscaling as appscaling
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_secretsmanager as sm
from constructs import Construct

from config import (EMBED_MODEL, GEO_FM_REGIONS, MODEL_CHEAP, MODEL_FALLBACK, MODEL_HARD, Sizing, act_role_name, api_role_name,
                    bootstrap_role_name, mock_role_name, worker_role_name)
from stacks.network_stack import API_PORT, MOCK_LISTENER_PORT, MOCK_PORT

WORKER_MAX_TASKS = 1       # see the scaling comment: total Nova Lite calls must stay under 10 RPM
REPO_ROOT = Path(__file__).resolve().parents[2]
ARM = ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64, operating_system_family=ecs.OperatingSystemFamily.LINUX)


def _fm(model_id: str, region: str) -> str:
    return f"arn:aws:bedrock:{region}::foundation-model/{model_id}"


def bedrock_statements(account: str, region: str, *, llm: bool, embed: bool) -> list[iam.PolicyStatement]:
    """InvokeModel only on the exact models in use. Geo profiles: the profile ARN plus the underlying FM in the profile's EU
    Regions, the latter only when the call arrives through that profile (bedrock:InferenceProfileArn)."""
    st: list[iam.PolicyStatement] = []
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]

    def geo(profile_id: str, fm_id: str) -> None:
        prof = f"arn:aws:bedrock:{region}:{account}:inference-profile/{profile_id}"
        st.append(iam.PolicyStatement(actions=actions, resources=[prof]))
        st.append(iam.PolicyStatement(
            actions=actions, resources=[_fm(fm_id, GEO_FM_REGIONS)],
            conditions={"StringLike": {"bedrock:InferenceProfileArn": prof}}))

    if llm:
        geo(MODEL_CHEAP, MODEL_CHEAP.removeprefix("eu."))
        st.append(iam.PolicyStatement(actions=actions, resources=[_fm(MODEL_HARD, region), _fm(MODEL_FALLBACK, region)]))
    if embed:
        geo(EMBED_MODEL, EMBED_MODEL.removeprefix("eu."))
    return st


class ComputeStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, sizing: Sizing, env_name: str, net, data, ops,
                 certificate_arn: str = "", escalation_backstop_minutes: int = 30, **kw):
        super().__init__(scope, cid, **kw)
        vpc = net.vpc
        region, account = self.region, self.account
        subnets = ec2.SubnetSelection(subnet_group_name="workload")
        destroy = RemovalPolicy.DESTROY

        # ------------------------------------------------------------------ secrets (generated; values never in the template)
        def gen_secret(cid_: str, desc: str, username: str | None = None) -> sm.Secret:
            if username:
                g = sm.SecretStringGenerator(secret_string_template=f'{{"username": "{username}"}}', generate_string_key="password",
                                             exclude_punctuation=True, password_length=32)
            else:
                g = sm.SecretStringGenerator(exclude_punctuation=True, password_length=40)
            return sm.Secret(self, cid_, description=desc, generate_secret_string=g, removal_policy=destroy)

        self.act_db_secret = gen_secret("ActDbSecret", "PayNest A7 a7_act DB login (PII vault reader)", "a7_act")
        self.admin_db_secret = gen_secret("AdminDbSecret", "PayNest A7 a7_admin DB login (feature_flags writer)", "a7_admin")
        self.reviewer_token = gen_secret("ReviewerToken", "PayNest A7 reviewer API bearer token")
        self.admin_token = gen_secret("AdminToken", "PayNest A7 /admin/flags bearer token")
        self.mock_token = gen_secret("MockToken", "PayNest A7 mock payroll API bearer token")
        app_user = data.app_user_secret
        master = data.db.secret

        # ------------------------------------------------------------------ cluster, logs, ALB
        self.cluster = ecs.Cluster(self, "Cluster", cluster_name=f"paynest-a7-{env_name}", vpc=vpc, container_insights=False)
        self.alb = elbv2.ApplicationLoadBalancer(
            self, "Alb", vpc=vpc, internet_facing=net.public_alb, vpc_subnets=net.alb_subnets, security_group=net.alb_sg,
            load_balancer_name=f"paynest-a7-{env_name}", deletion_protection=False)
        if net.public_alb:
            if not certificate_arn:
                raise ValueError("public_alb=true requires -c alb_certificate_arn=<ACM cert> (HTTPS only); also attach WAF")
            from aws_cdk import aws_certificatemanager as acm
            main_listener = self.alb.add_listener("Https", port=443, protocol=elbv2.ApplicationProtocol.HTTPS, open=False,
                                                  certificates=[acm.Certificate.from_certificate_arn(self, "Cert", certificate_arn)])
        else:
            main_listener = self.alb.add_listener("Http", port=80, open=False)     # internal; VPC-only ingress via alb_sg
        mock_listener = self.alb.add_listener("MockHttp", port=MOCK_LISTENER_PORT, protocol=elbv2.ApplicationProtocol.HTTP, open=False)

        # ------------------------------------------------------------------ images (ARM64; built at deploy time by the CDK CLI)
        def image(name: str, target: str) -> ecs.ContainerImage:
            return ecs.ContainerImage.from_docker_image_asset(ecr_assets.DockerImageAsset(
                self, f"Image-{name}", directory=str(REPO_ROOT), file="docker/Dockerfile", target=target,
                platform=ecr_assets.Platform.LINUX_ARM64))

        # ------------------------------------------------------------------ roles (explicit names: the KMS key policy keys on them)
        def role(cid_: str, name: str, desc: str) -> iam.Role:
            return iam.Role(self, cid_, role_name=name, assumed_by=iam.ServicePrincipal(
                "ecs-tasks.amazonaws.com", conditions={"StringEquals": {"aws:SourceAccount": account}}), description=desc)

        self.worker_role = role("WorkerRole", worker_role_name(env_name), "A7 worker: SQS consume, Bedrock, vault GenerateDataKey ONLY")
        self.api_role = role("ApiRole", api_role_name(env_name), "A7 reviewer API: Bedrock embeddings, schedule escalations")
        self.act_role = role("ActRole", act_role_name(env_name), "A7 act: the ONLY principal with kms:Decrypt on the vault key and the a7_act secret")
        self.mock_role = role("MockRole", mock_role_name(env_name), "A7 mock payroll: no AWS permissions")
        self.bootstrap_role = role("BootstrapRole", bootstrap_role_name(env_name), "A7 one-off bootstrap/ingest task")

        # worker
        self.worker_role.add_managed_policy(data.worker_vault_policy)
        self.worker_role.add_to_policy(iam.PolicyStatement(      # not grant_consume_messages(): it adds an unconditioned kms:Decrypt
            actions=["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"],
            resources=[data.ingest_queue.queue_arn]))
        for stmt in bedrock_statements(account, region, llm=True, embed=True):
            self.worker_role.add_to_policy(stmt)
        # api / bootstrap: embeddings only
        for r in (self.api_role, self.bootstrap_role):
            for stmt in bedrock_statements(account, region, llm=False, embed=True):
                r.add_to_policy(stmt)
        # act: vault Decrypt (+ the a7_act secret, below)
        self.act_role.add_managed_policy(data.act_vault_policy)

        # ------------------------------------------------------------------ escalation timers: EventBridge Scheduler -> SQS
        group_name = f"paynest-a7-{env_name}"
        sched_group = scheduler.CfnScheduleGroup(self, "ScheduleGroup", name=group_name)
        sched_group.apply_removal_policy(destroy)         # deleting the group deletes its (one-time) schedules
        self.scheduler_role = iam.Role(
            self, "SchedulerRole", role_name=f"paynest-a7-{env_name}-scheduler",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com", conditions={"StringEquals": {"aws:SourceAccount": account}}),
            description="EventBridge Scheduler: send escalation messages to the ingest queue")
        self.scheduler_role.add_to_policy(iam.PolicyStatement(actions=["sqs:SendMessage"], resources=[data.ingest_queue.queue_arn]))
        self.scheduler_role.add_to_policy(iam.PolicyStatement(
            actions=["kms:GenerateDataKey", "kms:Decrypt"], resources=[data.data_key.key_arn],
            conditions={"StringEquals": {"kms:ViaService": f"sqs.{region}.amazonaws.com"}}))
        sched_arn = f"arn:aws:scheduler:{region}:{account}:schedule/{group_name}/*"
        for r in (self.worker_role, self.api_role):
            r.add_to_policy(iam.PolicyStatement(actions=["scheduler:CreateSchedule"], resources=[sched_arn]))
            r.add_to_policy(iam.PolicyStatement(
                actions=["iam:PassRole"], resources=[self.scheduler_role.role_arn],
                conditions={"StringEquals": {"iam:PassedToService": "scheduler.amazonaws.com"}}))
        if escalation_backstop_minutes > 0:               # recurring run_due message: catches any one-time schedule that failed
            scheduler.CfnSchedule(
                self, "EscalationBackstop", name="a7-run-due", group_name=group_name,
                flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
                schedule_expression=f"rate({escalation_backstop_minutes} minutes)", state="ENABLED",
                target=scheduler.CfnSchedule.TargetProperty(
                    arn=data.ingest_queue.queue_arn, role_arn=self.scheduler_role.role_arn, input='{"run_due": true}'),
            ).add_dependency(sched_group)

        # ------------------------------------------------------------------ the a7_act secret: act role (+ bootstrap, to set the password)
        arn_of = lambda n: f"arn:aws:iam::{account}:role/{n}"   # noqa: E731
        allowed_readers = [arn_of(act_role_name(env_name)), arn_of(bootstrap_role_name(env_name))]
        self.act_db_secret.grant_read(self.act_role)
        self.act_db_secret.grant_read(self.bootstrap_role)
        self.act_db_secret.add_to_resource_policy(iam.PolicyStatement(
            sid="DenyAllButActAndBootstrap", effect=iam.Effect.DENY, principals=[iam.AnyPrincipal()],
            actions=["secretsmanager:GetSecretValue"], resources=["*"],
            conditions={"ArnNotEquals": {"aws:PrincipalArn": allowed_readers}}))
        for s in (app_user, self.admin_db_secret):
            s.grant_read(self.bootstrap_role)

        # ------------------------------------------------------------------ shared container environment (no secret values)
        common = {
            "A7_ENV": env_name, "AWS_REGION": region, "AWS_DEFAULT_REGION": region, "A7_AWS_PROFILE": "none",
            "A7_DB_HOST": data.db.db_instance_endpoint_address, "A7_DB_PORT": data.db.db_instance_endpoint_port, "A7_DB_NAME": "a7",
            "A7_VALKEY_URL": f"rediss://{data.cache.attr_primary_end_point_address}:{data.cache.attr_primary_end_point_port}/0",
            # Kill switch: the DB flag auto_send_enabled defaults OFF; env can only force OFF, never ON.
            "A7_AUTO_SEND": "false",
        }
        sched_env = {"A7_ESCALATION_BACKEND": "scheduler", "A7_SCHEDULER_GROUP": group_name,
                     "A7_ESCALATION_QUEUE_ARN": data.ingest_queue.queue_arn, "A7_SCHEDULER_ROLE_ARN": self.scheduler_role.role_arn}
        S = ecs.Secret
        app_pw = S.from_secrets_manager(app_user, "password")

        def taskdef(cid_: str, cpu: int, mem: int, role_: iam.Role) -> ecs.FargateTaskDefinition:
            return ecs.FargateTaskDefinition(self, cid_, cpu=cpu, memory_limit_mib=mem, runtime_platform=ARM, task_role=role_)

        def container(td, name: str, img, log_key: str, env: dict, secrets: dict, port: int | None = None,
                      command: list[str] | None = None, stop: int = 30):
            return td.add_container(
                name, image=img, environment=env, secrets=secrets, command=command, stop_timeout=Duration.seconds(stop),
                port_mappings=[ecs.PortMapping(container_port=port)] if port else None,
                logging=ecs.LogDrivers.aws_logs(stream_prefix=name, log_group=ops.log_groups[log_key]))

        def service(cid_: str, td, sg, desired: int, name: str) -> ecs.FargateService:
            return ecs.FargateService(
                self, cid_, service_name=f"{name}", cluster=self.cluster, task_definition=td, desired_count=desired,
                vpc_subnets=subnets, security_groups=[sg], assign_public_ip=False, platform_version=ecs.FargatePlatformVersion.LATEST,
                circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=False), min_healthy_percent=0, max_healthy_percent=200)

        # ---- api (behind the ALB)
        api_td = taskdef("ApiTask", 512, 2048, self.api_role)
        api_img = image("api", "api")
        container(api_td, "api", api_img, "api",
                  {**common, **sched_env},
                  {"A7_APP_USER_PASSWORD": app_pw, "A7_ADMIN_PASSWORD": S.from_secrets_manager(self.admin_db_secret, "password"),
                   "A7_REVIEWER_TOKEN": S.from_secrets_manager(self.reviewer_token), "A7_ADMIN_TOKEN": S.from_secrets_manager(self.admin_token)},
                  port=API_PORT)
        self.api_service = service("ApiService", api_td, net.api_sg, 1, f"paynest-a7-{env_name}-api")
        self.api_tg = main_listener.add_targets(
            "ApiTargets", port=API_PORT, protocol=elbv2.ApplicationProtocol.HTTP, targets=[self.api_service],
            # /livez has NO database call: on a first deploy the DB is empty (app_user has no valid login until the bootstrap task has run),
            # so a DB-backed check would never turn healthy and the stack would fail. /readyz (DB) is for the smoke task.
            health_check=elbv2.HealthCheck(path="/livez", healthy_http_codes="200", interval=Duration.seconds(30),
                                           healthy_threshold_count=2, unhealthy_threshold_count=3),
            deregistration_delay=Duration.seconds(10))

        # ---- mock payroll (demo only), reached by the worker through ALB:8081
        mock_td = taskdef("MockTask", 256, 512, self.mock_role)
        container(mock_td, "mock-payroll", image("mock", "mock-payroll"), "mock-payroll", {"A7_ENV": env_name},
                  {"A7_PAYROLL_API_TOKEN": S.from_secrets_manager(self.mock_token)}, port=MOCK_PORT)
        self.mock_service = service("MockService", mock_td, net.mock_sg, 1, f"paynest-a7-{env_name}-mock-payroll")
        self.mock_tg = mock_listener.add_targets(
            "MockTargets", port=MOCK_PORT, protocol=elbv2.ApplicationProtocol.HTTP, targets=[self.mock_service],
            health_check=elbv2.HealthCheck(path="/health", healthy_http_codes="200", interval=Duration.seconds(30)),
            deregistration_delay=Duration.seconds(10))

        # ---- worker (SQS-driven, 0..2)
        worker_td = taskdef("WorkerTask", 1024, 3072, self.worker_role)
        container(worker_td, "worker", image("worker", "worker"), "worker",
                  {**common, **sched_env, "A7_QUEUE_URL": data.ingest_queue.queue_url, "A7_KMS_KEY_ID": data.data_key.key_arn,
                   "A7_PAYROLL_API_URL": f"http://{self.alb.load_balancer_dns_name}:{MOCK_LISTENER_PORT}"},
                  {"A7_APP_USER_PASSWORD": app_pw, "A7_PAYROLL_API_TOKEN": S.from_secrets_manager(self.mock_token)}, stop=120)
        # SQS SSE-KMS: Decrypt only when called through SQS (the key policy denies every other non-act Decrypt)
        self.worker_role.add_to_policy(iam.PolicyStatement(
            actions=["kms:Decrypt"], resources=[data.data_key.key_arn],
            conditions={"StringEquals": {"kms:ViaService": f"sqs.{region}.amazonaws.com"}}))
        self.worker_service = service("WorkerService", worker_td, net.worker_sg, 0, f"paynest-a7-{env_name}-worker")
        depth = cw.MathExpression(
            expression="FILL(v, 0) + FILL(n, 0)", period=Duration.minutes(1),
            using_metrics={"v": data.ingest_queue.metric_approximate_number_of_messages_visible(),
                           "n": data.ingest_queue.metric_approximate_number_of_messages_not_visible()})
        # MAX 1 worker task (WORKER_MAX_TASKS): Nova Lite is limited to 10 requests per minute on-demand and each worker rate-limits itself to
        # 0.85 x 10 RPM, so two workers would together send about 17 RPM and be throttled. One worker stays under the quota; the p5-fin stale
        # check adds one LLM call per statutory draft. A second worker needs a shared (cross-task) rate limit first.
        scaling = self.worker_service.auto_scale_task_count(min_capacity=0, max_capacity=WORKER_MAX_TASKS)
        scaling.scale_on_metric(
            "QueueDepth", metric=depth, adjustment_type=appscaling.AdjustmentType.EXACT_CAPACITY,
            scaling_steps=[appscaling.ScalingInterval(upper=0, change=0), appscaling.ScalingInterval(lower=1, change=WORKER_MAX_TASKS)],
            cooldown=Duration.minutes(2), evaluation_periods=2)

        # ---- act (RunTask only; the one place that decrypts)
        act_td = taskdef("ActTask", 512, 1024, self.act_role)
        container(act_td, "act", image("act", "act"), "act",
                  {**common, "A7_KMS_KEY_ID": data.data_key.key_arn, "A7_ACT_SECRET_ARN": self.act_db_secret.secret_arn},
                  {"A7_APP_USER_PASSWORD": app_pw})
        self.act_td = act_td

        # ---- smoke (RunTask only): the internal ALB is unreachable from a laptop, so API calls for the gate run in-VPC (python -m solution.a7.smoke)
        self.smoke_role = role("SmokeRole", f"paynest-a7-{env_name}-smoke-task", "A7 in-VPC smoke client: HTTP calls plus SendMessage to the ingest queue (smoke batch)")
        # `smoke batch` replays ticket ids through the ingest queue: SendMessage on that one queue, and GenerateDataKey only through SQS (queue is SSE-KMS).
        self.smoke_role.add_to_policy(iam.PolicyStatement(actions=["sqs:SendMessage"], resources=[data.ingest_queue.queue_arn]))
        self.smoke_role.add_to_policy(iam.PolicyStatement(
            actions=["kms:GenerateDataKey"], resources=[data.data_key.key_arn],
            conditions={"StringEquals": {"kms:ViaService": f"sqs.{region}.amazonaws.com"}}))
        smoke_td = taskdef("SmokeTask", 256, 512, self.smoke_role)
        scheme = "https" if net.public_alb else "http"
        container(smoke_td, "smoke", api_img, "api",
                  {"A7_ALB_URL": f"{scheme}://{self.alb.load_balancer_dns_name}", "AWS_REGION": region, "A7_QUEUE_URL": data.ingest_queue.queue_url,
                   "A7_PAYROLL_API_URL": f"http://{self.alb.load_balancer_dns_name}:{MOCK_LISTENER_PORT}"},
                  {"A7_REVIEWER_TOKEN": S.from_secrets_manager(self.reviewer_token), "A7_ADMIN_TOKEN": S.from_secrets_manager(self.admin_token),
                   "A7_PAYROLL_API_TOKEN": S.from_secrets_manager(self.mock_token)},
                  command=["python", "-m", "solution.a7.smoke", "health"])
        self.smoke_td = smoke_td

        # ---- bootstrap / ingest (RunTask only)
        boot_td = taskdef("BootstrapTask", 512, 1024, self.bootstrap_role)
        container(boot_td, "bootstrap", image("bootstrap", "bootstrap"), "bootstrap",
                  {**common, "A7_DB_OWNER_USER": "pnadmin",
                   "A7_APP_USER_SECRET_ARN": app_user.secret_arn, "A7_ACT_SECRET_ARN": self.act_db_secret.secret_arn,
                   "A7_ADMIN_SECRET_ARN": self.admin_db_secret.secret_arn},
                  {"A7_DB_OWNER_PASSWORD": S.from_secrets_manager(master, "password")})
        self.bootstrap_td = boot_td

        # ------------------------------------------------------------------ outputs for the runbook
        subnet_ids = ",".join(s.subnet_id for s in vpc.select_subnets(subnet_group_name="workload").subnets[:1])
        net_cfg = f"awsvpcConfiguration={{subnets=[{subnet_ids}],securityGroups=[{net.worker_sg.security_group_id}],assignPublicIp=DISABLED}}"
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name)
        CfnOutput(self, "AlbDnsName", value=self.alb.load_balancer_dns_name)
        CfnOutput(self, "BootstrapTaskDefinition", value=boot_td.family)
        CfnOutput(self, "ActTaskDefinition", value=act_td.family)
        CfnOutput(self, "SmokeTaskDefinition", value=smoke_td.family)
        CfnOutput(self, "RunTaskNetworkConfiguration", value=net_cfg, description="--network-configuration value for aws ecs run-task")
        CfnOutput(self, "ReviewerTokenSecretArn", value=self.reviewer_token.secret_arn)
        CfnOutput(self, "AdminTokenSecretArn", value=self.admin_token.secret_arn)
        Tags.of(self).add("stack", "compute")
