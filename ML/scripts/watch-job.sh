#!/usr/bin/env bash
# Usage: ML/scripts/watch-job.sh model-a-dev-smoke-003
# Polls a SageMaker training job until it ends, then prints the failure reason or
# the final metrics. Zscaler: --no-verify-ssl. Git Bash: MSYS_NO_PATHCONV for /aws paths.
J="${1:?training job name}"
export MSYS_NO_PATHCONV=1
while true; do
  R=$(aws sagemaker describe-training-job --training-job-name "$J" --no-verify-ssl \
        --query '[TrainingJobStatus,SecondaryStatus]' --output text 2>/dev/null | tail -1)
  echo "$(date +%T) $R"
  case "$R" in Completed*|Failed*|Stopped*) break;; esac
  sleep 30
done
aws sagemaker describe-training-job --training-job-name "$J" --no-verify-ssl \
  --query '{status:TrainingJobStatus,fail:FailureReason,billable_s:BillableTimeInSeconds,metrics:FinalMetricDataList[].[MetricName,Value]}' --output json 2>/dev/null
