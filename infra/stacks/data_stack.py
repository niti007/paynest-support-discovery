"""DataStack: shared CMK (RDS + PII vault + SQS + Valkey), RDS PostgreSQL 17, Valkey 9, SQS ingest + DLQ."""
import json

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticache as elasticache
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_sqs as sqs
from constructs import Construct

from config import Sizing, act_role_name, worker_role_name
from stacks.ops_stack import protection_policy

# AWS services that use the key on a principal's behalf (kms:ViaService); used by the key-policy deny below and asserted by the synth tests.
VIA_SERVICES = ("rds", "elasticache", "sqs", "secretsmanager", "logs")
PG_VERSION =rds.PostgresEngineVersion.of("17.11", "17")  # proven in probe P3 (pgvector 0.8.2)


class DataStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, sizing: Sizing, env_name: str,
                 vpc: ec2.IVpc, db_sg: ec2.ISecurityGroup, cache_sg: ec2.ISecurityGroup, keep_data: bool = False, **kw):
        super().__init__(scope, cid, **kw)
        destroyable = sizing.destroyable           # dev: no deletion protection
        # Teardown flag (context keep_data=true): snapshot the database and retain the CMK instead of destroying them.
        destroy_data = destroyable and not keep_data
        removal = RemovalPolicy.DESTROY if destroy_data else RemovalPolicy.RETAIN       # the CMK (snapshots are encrypted with it)

        # ---------------------------------------------------------------- KMS (CMK, also the PII vault key)
        self.data_key = kms.Key(
            self, "DataKey", alias=f"alias/paynest-a7-{env_name}-data", enable_key_rotation=True,
            description="PayNest A7: RDS storage, PII vault data keys, SQS, Valkey at rest",
            removal_policy=removal, pending_window=Duration.days(7))
        # Vault separation of duties. kms:Decrypt is denied to every IAM role and user of this account except the act role, UNLESS the call
        # arrives through one of the integrated services (kms:ViaService in VIA_SERVICES). Why this shape (AWS KMS docs: condition keys
        # kms:ViaService, kms:GrantIsForAWSResource, aws:PrincipalIsAWSService; how RDS / ElastiCache / SQS use AWS KMS):
        #  * ViaService is only set for calls made on a principal's behalf (forward access sessions, e.g. SQS decrypting for the worker role).
        #  * RDS and ElastiCache (like EBS) decrypt with their OWN identity under a GRANT that the deployer's CreateGrant created
        #    (kms:GrantIsForAWSResource). Those calls carry no ViaService. The caller is a service principal or a service-linked role
        #    (role/aws-service-role/...), never one of our task roles or a human. So the deny is scoped to account roles and users
        #    (aws:PrincipalArn like role/* or user/*) and EXEMPTS service-linked roles; a service principal has no aws:PrincipalArn at all,
        #    so the ArnLike is false and the deny does not apply to it.
        #  * An explicit deny beats any allow or grant, so a deny that also caught the service callers would break RDS/Valkey creation.
        # Result: humans, the CDK execution role and every task role other than act cannot call Decrypt directly; AWS services still work.
        # The deploy itself proves it (RDS or Valkey would fail to create; the queue test would fail to decrypt); see infra/README.md.
        # Role names are a contract with ComputeStack (config.worker_role_name / act_role_name).
        self.data_key.add_to_resource_policy(iam.PolicyStatement(
            sid="DenyDirectDecryptExceptActRole", effect=iam.Effect.DENY,
            principals=[iam.AnyPrincipal()], actions=["kms:Decrypt"], resources=["*"],
            conditions={
                "ArnLike": {"aws:PrincipalArn": [f"arn:aws:iam::{self.account}:role/*", f"arn:aws:iam::{self.account}:user/*"]},
                "ArnNotLike": {"aws:PrincipalArn": [
                    f"arn:aws:iam::{self.account}:role/{act_role_name(env_name)}",
                    f"arn:aws:iam::{self.account}:role/aws-service-role/*"]},
                "StringNotEquals": {"kms:ViaService": [f"{svc}.{self.region}.amazonaws.com" for svc in VIA_SERVICES]},
            }))
        self.worker_vault_policy = iam.ManagedPolicy(
            self, "WorkerVaultPolicy", managed_policy_name=f"paynest-a7-{env_name}-worker-vault",
            description="Worker: GenerateDataKey ONLY on the PII vault key (no Decrypt)",
            statements=[iam.PolicyStatement(
                actions=["kms:GenerateDataKey"], resources=[self.data_key.key_arn])])
        self.act_vault_policy = iam.ManagedPolicy(
            self, "ActVaultPolicy", managed_policy_name=f"paynest-a7-{env_name}-act-vault",
            description="Act role: Decrypt on the PII vault key (the only principal allowed)",
            statements=[iam.PolicyStatement(
                actions=["kms:Decrypt", "kms:DescribeKey"], resources=[self.data_key.key_arn])])

        # ---------------------------------------------------------------- RDS PostgreSQL 17
        subnet_group = rds.SubnetGroup(
            self, "DbSubnets", vpc=vpc, description="PayNest A7 RDS (isolated subnets)",
            vpc_subnets=ec2.SubnetSelection(subnet_group_name="workload"), removal_policy=RemovalPolicy.DESTROY)
        # The PostgreSQL log-export group. RDS would auto-create it OUTSIDE CloudFormation (no retention, no data protection, survives destroy),
        # so it is created here first: 7-day retention, the same data protection policy as the app logs, DESTROY with the stack.
        db_id = f"paynest-a7-{env_name}-db"
        self.rds_log_group = logs.LogGroup(
            self, "RdsPostgresLogs", log_group_name=f"/aws/rds/instance/{db_id}/postgresql", retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY, data_protection_policy=protection_policy())
        self.db = rds.DatabaseInstance(
            self, "Db", instance_identifier=db_id, engine=rds.DatabaseInstanceEngine.postgres(version=PG_VERSION),
            instance_type=ec2.InstanceType(sizing.db_instance_class),
            vpc=vpc, subnet_group=subnet_group, security_groups=[db_sg], publicly_accessible=False,
            credentials=rds.Credentials.from_username("pnadmin"), manage_master_user_password=True,
            database_name="a7",
            storage_encrypted=True, storage_encryption_key=self.data_key,
            storage_type=rds.StorageType.GP3, allocated_storage=sizing.db_allocated_gb,
            multi_az=False, backup_retention=Duration.days(sizing.db_backup_days),
            deletion_protection=not destroyable,
            removal_policy=RemovalPolicy.DESTROY if destroy_data else RemovalPolicy.SNAPSHOT,
            delete_automated_backups=destroy_data,
            auto_minor_version_upgrade=True, cloudwatch_logs_exports=["postgresql"])

        self.db.node.add_dependency(self.rds_log_group)

        # App-user password (schema.sql creates role app_user with a dev password; bootstrap overwrites it)
        self.app_user_secret = sm.Secret(
            self, "AppUserSecret", description="PayNest A7 app_user (RLS) password",
            generate_secret_string=sm.SecretStringGenerator(
                secret_string_template=json.dumps({"username": "app_user"}), generate_string_key="password",
                exclude_punctuation=True, password_length=32),
            removal_policy=RemovalPolicy.DESTROY)      # regenerable: the bootstrap task re-sets the role password from it

        # ---------------------------------------------------------------- Valkey 9
        cache_subnets = elasticache.CfnSubnetGroup(
            self, "CacheSubnets", description="PayNest A7 Valkey",
            subnet_ids=vpc.select_subnets(subnet_group_name="workload").subnet_ids)
        self.cache = elasticache.CfnReplicationGroup(
            self, "Cache", replication_group_description="PayNest A7 Valkey (single node)",
            engine="valkey", engine_version="9.0", cache_node_type=sizing.cache_node_type,
            num_cache_clusters=1, automatic_failover_enabled=False, multi_az_enabled=False,
            transit_encryption_enabled=True, at_rest_encryption_enabled=True, kms_key_id=self.data_key.key_arn,
            cache_subnet_group_name=cache_subnets.ref, security_group_ids=[cache_sg.security_group_id],
            port=6379, auto_minor_version_upgrade=True)

        # ---------------------------------------------------------------- SQS ingest + DLQ (CMK)
        self.dlq = sqs.Queue(
            self, "IngestDlq", queue_name=f"paynest-a7-{env_name}-ingest-dlq",
            encryption=sqs.QueueEncryption.KMS, encryption_master_key=self.data_key,
            enforce_ssl=True, retention_period=Duration.days(14), removal_policy=RemovalPolicy.DESTROY)
        self.ingest_queue = sqs.Queue(
            self, "IngestQueue", queue_name=f"paynest-a7-{env_name}-ingest",
            encryption=sqs.QueueEncryption.KMS, encryption_master_key=self.data_key,
            enforce_ssl=True, visibility_timeout=Duration.seconds(300), retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=5, queue=self.dlq),
            removal_policy=RemovalPolicy.DESTROY)

        # ---------------------------------------------------------------- outputs (consumed by ComputeStack)
        CfnOutput(self, "DbEndpoint", value=self.db.db_instance_endpoint_address)
        CfnOutput(self, "DbMasterSecretArn", value=self.db.secret.secret_arn if self.db.secret else "n/a")
        CfnOutput(self, "AppUserSecretArn", value=self.app_user_secret.secret_arn)
        CfnOutput(self, "ValkeyEndpoint", value=self.cache.attr_primary_end_point_address)
        CfnOutput(self, "ValkeyPort", value=self.cache.attr_primary_end_point_port)
        CfnOutput(self, "IngestQueueUrl", value=self.ingest_queue.queue_url)
        CfnOutput(self, "IngestDlqUrl", value=self.dlq.queue_url)
        CfnOutput(self, "PiiKeyArn", value=self.data_key.key_arn)
        CfnOutput(self, "WorkerVaultPolicyArn", value=self.worker_vault_policy.managed_policy_arn,
                  description=f"Attach to role {worker_role_name(env_name)}: kms:GenerateDataKey only")
        CfnOutput(self, "ActVaultPolicyArn", value=self.act_vault_policy.managed_policy_arn,
                  description=f"Attach to role {act_role_name(env_name)}: kms:Decrypt")
        Tags.of(self).add("stack", "data")
