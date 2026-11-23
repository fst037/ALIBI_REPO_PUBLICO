#!/usr/bin/env bash
# Launch the Module A checkpoint comparison (src/model_a/eval_checkpoints.py) as a SageMaker
# training job: the untuned base plus every checkpoint of a training run, scored on the FULL
# BIRD dev + Spider dev sets. Run from anywhere:
#
#   ML/scripts/launch-eval-model-a.sh eval-002              # run id; job = model-a-dev-<run id>
#   INSTANCE=ml.g6e.2xlarge CKPTS="2110 1052" ML/scripts/launch-eval-model-a.sh eval-003
#
# The job runs the tarball this script builds from the CURRENT working tree and uploads to
# s3://<artifacts>/code/model_a_eval_<run id>.tar.gz — commit before running, so the code that
# produced a result is the code in git (the commit is recorded in the job's tags).
#
# Prerequisites (one-off): the eval rows at s3://<datasets>/model_a/eval/eval.jsonl, the BIRD dev
# and Spider databases at s3://<datasets>/model_a/raw/{bird/dev_databases,spider/database}/
# (*.sqlite only), and the training run's checkpoints under s3://<artifacts>/model_a/checkpoints/.
# On this machine AWS calls need --no-verify-ssl (Zscaler); set AWS_FLAGS="" elsewhere.
set -euo pipefail

RUN_ID="${1:?usage: launch-eval-model-a.sh <run-id>   e.g. eval-002}"
TRAIN_RUN="${TRAIN_RUN:-qlora-001}"                       # whose checkpoints to compare
CKPTS="${CKPTS:-263 526 1052 2110}"   # checkpoint-<step> directories
INSTANCE="${INSTANCE:-ml.g6e.xlarge}"                     # one training job per type at a time
MAX_RUNTIME="${MAX_RUNTIME:-72000}"                       # 20 h hard stop; results are written per variant
PER_SOURCE="${PER_SOURCE:-0}"                             # 0 = the full dev sets
BATCH_SIZE="${BATCH_SIZE:-64}"                           # prompts per batch, further capped by the token budget
TOKEN_BUDGET="${TOKEN_BUDGET:-40000}"                     # padded prompt tokens per batch
AWS_FLAGS="${AWS_FLAGS---no-verify-ssl}"

DATASETS=s3://alibi-datasets-dev-213598695817
ARTIFACTS=s3://alibi-model-artifacts-dev-213598695817
ROLE=arn:aws:iam::213598695817:role/AlibiSageMakerRole-dev
IMAGE=763104351884.dkr.ecr.us-east-1.amazonaws.com/huggingface-pytorch-training:2.9.0-transformers5.3.0-gpu-py312-cu130-ubuntu22.04-v1.0
JOB="model-a-dev-${RUN_ID}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOT="$(cygpath -m "$ROOT" 2>/dev/null || echo "$ROOT")"  # Windows git/aws want C:/ paths
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

if [ -n "$(git -C "$ROOT" status --porcelain -- ML/src)" ]; then
  echo "ML/src has uncommitted changes — commit first so the result can be traced to code." >&2
  exit 1
fi
COMMIT="$(git -C "$ROOT" rev-parse --short HEAD)"

# Same layout the training tarball uses (model_a/ + common/ + root requirements.txt), plus sqlglot
# for the guardrail column.
cp -r "$ROOT/ML/src/model_a" "$ROOT/ML/src/common" "$STAGE/"
find "$STAGE" -name __pycache__ -prune -exec rm -rf {} +
cp "$ROOT/ML/src/requirements.txt" "$STAGE/requirements.txt"
printf '\nsqlglot>=23.0\n' >> "$STAGE/requirements.txt"
TARBALL="$STAGE/model_a_eval.tar.gz"
tar --force-local -czf "$TARBALL" -C "$STAGE" model_a common requirements.txt
native() { cygpath -m "$1" 2>/dev/null || echo "$1"; }  # Windows aws.exe cannot read /tmp paths
aws s3 cp $AWS_FLAGS "$(native "$TARBALL")" "$ARTIFACTS/code/model_a_eval_${RUN_ID}.tar.gz" >/dev/null

channel() {  # name, s3 prefix
  printf '{"ChannelName":"%s","DataSource":{"S3DataSource":{"S3DataType":"S3Prefix","S3Uri":"%s","S3DataDistributionType":"FullyReplicated"}}}' "$1" "$2"
}
CHANNELS="$(channel eval "$DATASETS/model_a/eval/"),$(channel bird "$DATASETS/model_a/raw/bird/dev_databases/"),$(channel spider "$DATASETS/model_a/raw/spider/database/")"
for step in $CKPTS; do
  CHANNELS="$CHANNELS,$(channel "ckpt_s${step}" "$ARTIFACTS/model_a/checkpoints/${TRAIN_RUN}/checkpoint-${step}/")"
done

CONFIG="$STAGE/job.json"
cat > "$CONFIG" <<EOF
{
 "TrainingJobName": "$JOB",
 "RoleArn": "$ROLE",
 "AlgorithmSpecification": {"TrainingImage": "$IMAGE", "TrainingInputMode": "File"},
 "HyperParameters": {"sagemaker_program": "model_a/eval_checkpoints.py",
                     "sagemaker_submit_directory": "$ARTIFACTS/code/model_a_eval_${RUN_ID}.tar.gz",
                     "per_source": "$PER_SOURCE", "batch_size": "$BATCH_SIZE", "token_budget": "$TOKEN_BUDGET"},
 "InputDataConfig": [$CHANNELS],
 "OutputDataConfig": {"S3OutputPath": "$ARTIFACTS/model_a/eval/${RUN_ID}/"},
 "ResourceConfig": {"InstanceType": "$INSTANCE", "InstanceCount": 1, "VolumeSizeInGB": 200},
 "StoppingCondition": {"MaxRuntimeInSeconds": $MAX_RUNTIME},
 "Tags": [{"Key": "git_commit", "Value": "$COMMIT"}, {"Key": "checkpoints_of", "Value": "$TRAIN_RUN"}]
}
EOF
aws sagemaker create-training-job $AWS_FLAGS --cli-input-json "file://$(native "$CONFIG")"
echo "submitted $JOB on $INSTANCE at commit $COMMIT"
echo "results: $ARTIFACTS/model_a/eval/${RUN_ID}/$JOB/output/output.tar.gz (metrics.json, predictions_*.jsonl)"
