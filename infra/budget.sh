#!/usr/bin/env bash
# One-off: the gb-grid-map monthly cost budget ($5; email at 80% actual and 100% forecast).
# Lives outside hosting.yaml because CloudFormation in eu-north-1 has no AWS::Budgets::Budget.
# Usage: infra/budget.sh you@example.com
set -euo pipefail
EMAIL=${1:?alert email}
export AWS_PROFILE="${AWS_PROFILE:-gb-grid-map}"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
SUBS="[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$EMAIL\"}]"
aws budgets create-budget --region us-east-1 --account-id "$ACCOUNT" \
  --budget '{"BudgetName":"gb-grid-map-monthly","BudgetType":"COST","TimeUnit":"MONTHLY","BudgetLimit":{"Amount":"5","Unit":"USD"}}' \
  --notifications-with-subscribers "[
    {\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":80,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":$SUBS},
    {\"Notification\":{\"NotificationType\":\"FORECASTED\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":100,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":$SUBS}]"
