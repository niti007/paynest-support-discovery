"""MonitoringStack: alarms (-> SNS topic from OpsStack) and a small dashboard. Split from OpsStack because it needs the ALB,
target groups, queues and DB from Compute/Data while Compute needs the log groups from Ops (a single stack would be a cycle)."""
from aws_cdk import Duration, Stack, Tags
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_logs as logs
from constructs import Construct

from config import EMBED_MODEL, MODEL_CHEAP, MODEL_FALLBACK, MODEL_HARD

BEDROCK_MODELS = (MODEL_CHEAP, MODEL_HARD, MODEL_FALLBACK, EMBED_MODEL)
NS = "PayNest/A7"


class MonitoringStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, data, compute, ops, **kw):
        super().__init__(scope, cid, **kw)
        topic_action = cw_actions.SnsAction(ops.alarm_topic)
        self.alarms: dict[str, cw.Alarm] = {}

        def alarm(key: str, metric, threshold: float, desc: str, *, op=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                  periods: int = 1, missing=cw.TreatMissingData.NOT_BREACHING) -> cw.Alarm:
            a = cw.Alarm(self, key, alarm_name=f"paynest-a7-{env_name}-{key}", alarm_description=desc, metric=metric,
                         threshold=threshold, evaluation_periods=periods, comparison_operator=op, treat_missing_data=missing)
            a.add_alarm_action(topic_action)
            self.alarms[key] = a
            return a

        five = Duration.minutes(5)
        alb, api_tg, mock_tg = compute.alb, compute.api_tg, compute.mock_tg

        alarm("dlq-depth", data.dlq.metric_approximate_number_of_messages_visible(period=Duration.minutes(1)), 0,
              "A message is in the ingest dead-letter queue (failed 5 times)")
        alarm("alb-5xx", cw.MathExpression(
            expression="FILL(e, 0) + FILL(t, 0)", period=five,
            using_metrics={"e": alb.metrics.http_code_elb(elbv2_code(), period=five), "t": alb.metrics.http_code_target(elbv2_target_code(), period=five)}),
            0, "ALB or target 5xx responses")
        alarm("unhealthy-hosts", cw.MathExpression(
            expression="FILL(a, 0) + FILL(m, 0)", period=Duration.minutes(1),
            using_metrics={"a": api_tg.metrics.unhealthy_host_count(period=Duration.minutes(1)),
                           "m": mock_tg.metrics.unhealthy_host_count(period=Duration.minutes(1))}),
            0, "api or mock-payroll target is unhealthy", periods=3)

        # worker errors: the worker logs "message failed: <ExceptionType>" (masked: type name only)
        wf = logs.MetricFilter(self, "WorkerErrorFilter", log_group=ops.log_groups["worker"], metric_namespace=NS,
                               metric_name="WorkerMessageFailed", filter_pattern=logs.FilterPattern.literal('"message failed"'),
                               metric_value="1", default_value=0)
        alarm("worker-errors", wf.metric(statistic="Sum", period=five), 0, "Worker failed to process a queue message")

        alarm("rds-cpu", data.db.metric_cpu_utilization(period=five), 80, "RDS CPU above 80% for 15 min", periods=3)
        alarm("rds-storage", data.db.metric_free_storage_space(period=five), 2 * 1024**3,
              "RDS free storage below 2 GiB", op=cw.ComparisonOperator.LESS_THAN_THRESHOLD)

        throttles = [cw.Metric(namespace="AWS/Bedrock", metric_name="InvocationThrottles", statistic="Sum", period=five,
                               dimensions_map={"ModelId": m}) for m in BEDROCK_MODELS]
        thr_expr = cw.MathExpression(expression=" + ".join(f"FILL(t{i}, 0)" for i in range(len(throttles))), period=five,
                                     using_metrics={f"t{i}": m for i, m in enumerate(throttles)})
        alarm("bedrock-throttles", thr_expr, 0, "Bedrock throttled an invocation (Nova Lite is limited to 10 RPM on-demand)")

        # ---- dashboard
        d = cw.Dashboard(self, "Dashboard", dashboard_name=f"paynest-a7-{env_name}", default_interval=Duration.hours(3))
        W = lambda title, *m, **k: cw.GraphWidget(title=title, left=list(m), width=8, height=6, **k)  # noqa: E731
        d.add_widgets(
            W("ALB requests / 5xx", alb.metrics.request_count(period=five), alb.metrics.http_code_elb(elbv2_code(), period=five),
              alb.metrics.http_code_target(elbv2_target_code(), period=five)),
            W("API target response time (s)", api_tg.metrics.target_response_time(period=five)),
            W("Healthy / unhealthy targets", api_tg.metrics.healthy_host_count(period=five), api_tg.metrics.unhealthy_host_count(period=five),
              mock_tg.metrics.unhealthy_host_count(period=five)))
        d.add_widgets(
            W("Ingest queue / DLQ", data.ingest_queue.metric_approximate_number_of_messages_visible(period=Duration.minutes(1)),
              data.ingest_queue.metric_approximate_number_of_messages_not_visible(period=Duration.minutes(1)),
              data.dlq.metric_approximate_number_of_messages_visible(period=Duration.minutes(1))),
            W("ECS CPU %", compute.api_service.metric_cpu_utilization(period=five), compute.worker_service.metric_cpu_utilization(period=five),
              compute.mock_service.metric_cpu_utilization(period=five)),
            W("Worker running tasks / failures", wf.metric(statistic="Sum", period=five)))
        d.add_widgets(
            W("RDS CPU % / connections", data.db.metric_cpu_utilization(period=five), data.db.metric_database_connections(period=five)),
            W("RDS free storage (bytes)", data.db.metric_free_storage_space(period=five)),
            W("Bedrock throttles", *throttles))
        Tags.of(self).add("stack", "monitoring")


def elbv2_code():
    from aws_cdk import aws_elasticloadbalancingv2 as elbv2
    return elbv2.HttpCodeElb.ELB_5XX_COUNT


def elbv2_target_code():
    from aws_cdk import aws_elasticloadbalancingv2 as elbv2
    return elbv2.HttpCodeTarget.TARGET_5XX_COUNT
