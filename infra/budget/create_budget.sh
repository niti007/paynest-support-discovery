#!/usr/bin/env bash
# Creates / deletes the monthly AWS Budget with alerts at 50/80/100% of actual spend. Budgets is a global API (no CloudFormation
# resource in eu-north-1), so this is a runbook step, not a stack resource. Needs the ops stack deployed (SNS topic).
#   BUDGET_USD=20 ALARM_EMAIL= ENV_NAME=dev AWS_PROFILE=paynest infra/budget/create_budget.sh create|delete
set -euo pipefail
ENV_NAME="${ENV_NAME:-dev}"; BUDGET_USD="${BUDGET_USD:-20}"; ALARM_EMAIL="${ALARM_EMAIL:-}"
NAME="paynest-a7-${ENV_NAME}-monthly"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
TOPIC="arn:aws:sns:eu-north-1:${ACCOUNT}:paynest-a7-${ENV_NAME}-alarms"
if [ "${1:-create}" = "delete" ]; then aws budgets delete-budget --account-id "$ACCOUNT" --budget-name "$NAME"; exit 0; fi
SUBS="[{\"SubscriptionType\":\"SNS\",\"Address\":\"$TOPIC\"}"
[ -n "$ALARM_EMAIL" ] && SUBS="$SUBS,{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$ALARM_EMAIL\"}"
SUBS="$SUBS]"
NOTIFS="["; SEP=""
for P in 50 80 100; do
  NOTIFS="$NOTIFS$SEP{\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":$P,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":$SUBS}"; SEP=","
done
NOTIFS="$NOTIFS]"
aws budgets create-budget --account-id "$ACCOUNT" \
  --budget "{\"BudgetName\":\"$NAME\",\"BudgetType\":\"COST\",\"TimeUnit\":\"MONTHLY\",\"BudgetLimit\":{\"Amount\":\"$BUDGET_USD\",\"Unit\":\"USD\"}}" \
  --notifications-with-subscribers "$NOTIFS"
echo "budget $NAME created: USD $BUDGET_USD, alerts at 50/80/100%"
