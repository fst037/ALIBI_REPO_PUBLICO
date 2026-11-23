#!/usr/bin/env bash
# Launch the Module C checkpoint generation (src/model_c/generate_checkpoints.py) as a SageMaker
# training job: the untuned base plus chosen checkpoints of a training run, on the full eval set.
# Scoring happens afterwards, locally, with model_c/score_predictions.py. Same conventions as
# launch-eval-model-a.sh (clean ML/src required, commit recorded in the job tags, code tarball
# built from the working tree).
#
#   ML/scripts/launch-eval-model-c.sh eval-001            # job = model-c-dev-<run id>
#   CKPTS="172 690" INSTANCE=ml.g6e.2xlarge ML/scripts/launch-eval-model-c.sh eval-002
set -euo pipefail

RUN_ID="${1:?usage: launch-eval-model-c.sh <run-id>   e.g. eval-001}"
TRAIN_RUN="${TRAIN_RUN:-qlora-002}"
CKPTS="${CKPTS:-172 344 516 690}"          # checkpoint-<step>: epoch 0.5 (best eval loss), 1, 1.5, final
INSTANCE="${INSTANCE:-ml.g6e.xlarge}"
MAX_RUNTIME="${MAX_RUNTIME:-43200}"
BATCH_SIZE="${BATCH_SIZE:-64}"
TOKEN_BUDGET="${TOKEN_BUDGET:-40000}"
AWS_FLAGS="${AWS_FLAGS---no-verify-ssl}"

DATASETS=s3://alibi-datasets-dev-213598695817
ARTIFACTS=s3://alibi-model-artifacts-dev-213598695817
ROLE=arn:aws:iam::213598695817:role/AlibiSageMakerRole-dev
IMAGE=763104351884.dkr.ecr.us-east-1.amazonaws.com/huggingface-pytorch-training:2.9.0-transformers5.3.0-gpu-py312-cu130-ubuntu22.04-v1.0
JOB="model-c-dev-${RUN_ID}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ROOT="$(cygpath -m "$ROOT" 2>/dev/null || echo "$ROOT")"  # Windows git/aws want C:/ paths
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
native() { cygpath -m "$1" 2>/dev/null || echo "$1"; }

if [ -n "$(git -C "$ROOT" status --porcelain -- ML/src)" ]; then
  echo "ML/src has uncommitted changes — commit first so the result can be traced to code." >&2
  exit 1
fi
COMMIT="$(git -C "$ROOT" rev-parse --short HEAD)"

# model_c/ + model_a/ (generate/load_model are imported from there) + common/ + requirements.txt
cp -r "$ROOT/ML/src/model_a" "$ROOT/ML/src/model_c" "$ROOT/ML/src/common" "$STAGE/"
find "$STAGE" -name __pycache__ -prune -exec rm -rf {} +
cp "$ROOT/ML/src/requirements.txt" "$STAGE/requirements.txt"
printf '\nsqlglot>=23.0\n' >> "$STAGE/requirements.txt"
TARBALL="$STAGE/model_c_eval.tar.gz"
tar --force-local -czf "$TARBALL" -C "$STAGE" model_a model_c common requirements.txt
aws s3 cp $AWS_FLAGS "$(native "$TARBALL")" "$ARTIFACTS/code/model_c_eval_${RUN_ID}.tar.gz" >/dev/null

channel() { printf '{"ChannelName":"%s","DataSource":{"S3DataSource":{"S3DataType":"S3Prefix","S3Uri":"%s","S3DataDistributionType":"FullyReplicated"}}}' "$1" "$2"; }
CHANNELS="$(channel eval "$DATASETS/model_c/eval/")"
for step in $CKPTS; do
  CHANNELS="$CHANNELS,$(channel "ckpt_s${step}" "$ARTIFACTS/model_c/checkpoints/${TRAIN_RUN}/checkpoint-${step}/")"
done

CONFIG="$STAGE/job.json"
cat > "$CONFIG" <<EOF
{
 "TrainingJobName": "$JOB",
 "RoleArn": "$ROLE",
 "AlgorithmSpecification": {"TrainingImage": "$IMAGE", "TrainingInputMode": "File"},
 "HyperParameters": {"sagemaker_program": "model_c/generate_checkpoints.py",
                     "sagemaker_submit_directory": "$ARTIFACTS/code/model_c_eval_${RUN_ID}.tar.gz",
                     "batch_size": "$BATCH_SIZE", "token_budget": "$TOKEN_BUDGET"},
 "InputDataConfig": [$CHANNELS],
 "OutputDataConfig": {"S3OutputPath": "$ARTIFACTS/model_c/eval/${RUN_ID}/"},
 "ResourceConfig": {"InstanceType": "$INSTANCE", "InstanceCount": 1, "VolumeSizeInGB": 150},
 "StoppingCondition": {"MaxRuntimeInSeconds": $MAX_RUNTIME},
 "Tags": [{"Key": "git_commit", "Value": "$COMMIT"}, {"Key": "checkpoints_of", "Value": "$TRAIN_RUN"}]
}
EOF
aws sagemaker create-training-job $AWS_FLAGS --cli-input-json "file://$(native "$CONFIG")"
echo "submitted $JOB on $INSTANCE at commit $COMMIT"
echo "results: $ARTIFACTS/model_c/eval/${RUN_ID}/$JOB/output/output.tar.gz (predictions_*.jsonl)"
