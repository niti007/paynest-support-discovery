"""OpsStack: log groups (retention + CloudWatch Logs data protection), SNS alarm topic, SNS topic (the budget itself is a script, see below).

Deployed BEFORE ComputeStack (it owns the log groups the tasks write to). Alarms and the dashboard need Compute's ALB,
target groups and queues, so they live in MonitoringStack (monitoring_stack.py) to avoid a stack dependency cycle.

Data protection: managed identifiers where AWS has one that fits, custom regex identifiers for the rest. AWS has NO managed
identifier for India (Aadhaar, PAN) or UAE (Emirates ID); BankAccountNumber only covers DE/ES/FR/GB/IT/US (IBAN with keyword),
and PhoneNumber only BR/DE/ES/FR/GB/IT/US. Custom identifiers: max 10 per policy, regex max 200 characters, and parentheses
are NOT in the allowed character set, so every pattern below is written without groups.
"""
from aws_cdk import CfnOutput, RemovalPolicy, Stack, Tags
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

LOG_COMPONENTS = ("api", "worker", "act", "mock-payroll", "bootstrap")
RETENTION = {7: logs.RetentionDays.ONE_WEEK, 14: logs.RetentionDays.TWO_WEEKS, 30: logs.RetentionDays.ONE_MONTH}

# name -> regex (custom data identifiers). No parentheses (not allowed by CloudWatch Logs).
CUSTOM_IDENTIFIERS = {
    # AE IBAN (AE + 2 check digits + 19 digits) in any 4-grouping, also other IBAN shapes with 4-char groups.
    "IbanAny": r"\b[A-Z]{2}[0-9]{2}[ -]?[A-Z0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{1,4}\b",
    # Emirates ID 784-YYYY-NNNNNNN-C, separators optional.
    "EmiratesId": r"\b784[ -]?[0-9]{4}[ -]?[0-9]{7}[ -]?[0-9]\b",
    # Aadhaar: 4-4-4 grouped by space or hyphen, or 12 contiguous digits starting 2-9. KNOWN OVER-MASK: a 12-digit AWS account id that starts
    # 2-9 (e.g. inside an ARN) is masked too. Accepted: excluding an ARN prefix needs a lookbehind, i.e. parentheses, which CloudWatch Logs
    # does not allow; masking an account id in a log line is harmless.
    "AadhaarIN": r"\b[0-9]{4}[ -][0-9]{4}[ -][0-9]{4}\b|\b[2-9][0-9]{11}\b",
    # Indian PAN: 5 letters, 4 digits, 1 letter.
    "PanIN": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
    # Indian bank account (A/C 9-18 digits) and IFSC. ':' is NOT an allowed character in a CloudWatch Logs custom identifier, so the
    # separator after IFSC is 0-3 non-alphanumerics instead of [: ]*.
    "BankAcctIfscIN": r"A/C[ ]?[0-9]{9,18}|IFSC[^A-Za-z0-9]{0,3}[A-Z]{4}0[A-Z0-9]{6}",
    # Phone: international +CC form, or Indian 10-digit mobile.
    "PhoneIntl": r"\+[0-9]{1,3}[ -]?[0-9]{1,5}[ -]?[0-9]{3,5}[ -]?[0-9]{3,5}|\b[6-9][0-9]{4}[ -]?[0-9]{5}\b",
}
MANAGED_IDENTIFIERS = [
    logs.DataIdentifier.EMAILADDRESS, logs.DataIdentifier.CREDITCARDNUMBER, logs.DataIdentifier.CREDITCARDSECURITYCODE,
    logs.DataIdentifier.BANKACCOUNTNUMBER_DE, logs.DataIdentifier.BANKACCOUNTNUMBER_ES, logs.DataIdentifier.BANKACCOUNTNUMBER_FR,
    logs.DataIdentifier.BANKACCOUNTNUMBER_GB, logs.DataIdentifier.BANKACCOUNTNUMBER_IT,
    logs.DataIdentifier.AWSSECRETKEY,
]


def protection_policy() -> logs.DataProtectionPolicy:
    custom = [logs.CustomDataIdentifier(n, rx) for n, rx in CUSTOM_IDENTIFIERS.items()]
    return logs.DataProtectionPolicy(name="paynest-a7-mask-pii", identifiers=[*MANAGED_IDENTIFIERS, *custom],
                                     description="Mask IBAN, PAN, Emirates ID, Aadhaar, email, phone, cards, bank accounts")


class OpsStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, retention_days: int = 7, alarm_email: str = "",
                 budget_usd: float = 20.0, **kw):
        super().__init__(scope, cid, **kw)
        if retention_days not in RETENTION:
            raise ValueError(f"log_retention_days must be one of {sorted(RETENTION)}")

        # ---- log groups (all DESTROY: logs are masked at ingestion and only meaningful during the demo window)
        self.log_groups: dict[str, logs.LogGroup] = {}
        policy = protection_policy()
        for comp in LOG_COMPONENTS:
            self.log_groups[comp] = logs.LogGroup(
                self, f"Logs-{comp}", log_group_name=f"/paynest/a7/{env_name}/{comp}", retention=RETENTION[retention_days],
                removal_policy=RemovalPolicy.DESTROY, data_protection_policy=policy)

        # ---- alarm topic. Alarm and budget payloads carry no PII; SSE with the AWS-managed key would block CloudWatch/Budgets
        # from publishing, so the topic is unencrypted with TLS enforced (cdk-nag SNS2 suppressed with this reason in app.py).
        self.alarm_topic = sns.Topic(self, "AlarmTopic", topic_name=f"paynest-a7-{env_name}-alarms",
                                     display_name="PayNest A7 alarms", enforce_ssl=True)
        self.alarm_topic.add_to_resource_policy(iam.PolicyStatement(
            sid="AllowBudgetsPublish", principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
            actions=["sns:Publish"], resources=[self.alarm_topic.topic_arn],
            conditions={"StringEquals": {"aws:SourceAccount": self.account}}))
        if alarm_email:                                        # CDK context alarm_email; empty by default, never hard-coded
            self.alarm_topic.add_subscription(subs.EmailSubscription(alarm_email))

        # ---- monthly budget: NOT a CloudFormation resource here. AWS::Budgets::Budget does not exist in eu-north-1 (Budgets is a global
        # service whose CFN resource is only offered in us-east-1, and this project may not create resources there). The Budgets API itself
        # works from eu-north-1 (probe P1 ops), so infra/budget/create_budget.sh creates it; the SNS topic policy above lets it publish.
        CfnOutput(self, "BudgetUsd", value=str(budget_usd), description="Monthly budget limit (context budget_usd); alerts at 50/80/100% via infra/budget/create_budget.sh")
        CfnOutput(self, "AlarmTopicArn", value=self.alarm_topic.topic_arn)
        Tags.of(self).add("stack", "ops")
