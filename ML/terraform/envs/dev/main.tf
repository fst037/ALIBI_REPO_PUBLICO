# ALIBI ML — dev environment
#
# Two modules: a SageMaker Training Job config, and the inference endpoint.
#
# IAM/storage/billing/ingestion Lambda are NOT owned by this root — they were
# migrated into terraform/envs/dev (the unified root) on 2026-07-13,
#as module.ml_iam/ml_storage/ml_billing/
# ml_ingestion_lambda. So sagemaker_role_arn/datasets_bucket/artifacts_bucket/
# etc. below are plain input variables, sourced from that root's outputs.
#
# There used to be four training backends behind a `training_backend` selector
# (lambda / ec2 / external_gpu / sagemaker), each `count = <selector matches>`.
# Three of them existed only because SageMaker GPU quota was unavailable; that
# quota was approved 2026-09-06, so they were deleted 2026-09-07 along with the
# selector. If a cheaper backend is ever needed again,
# git history has all three modules intact.
#
# There was also an MLflow tracking server here (first an always-on EC2, then
# a Lambda behind a Function URL) — removed 2026-09-28. `training`'s MetricDefinitions (see that module) publish
# loss/eval-loss/learning-rate/grad-norm/epoch straight to the training job's
# own CloudWatch console for $0 and no infrastructure, which is what the
# server actually existed for. The pipeline's own MLflow tracking (params,
# artifacts, the model registry — see `configure_mlflow()` in
# src/model_a/pipeline/_common.py) is unaffected: it always ran against a
# local file store and never needed this server.
#
# Apply order:
#   1. Core infra lives in the OTHER root:
#        cd terraform/envs/dev && terraform apply -target=module.ml_iam -target=module.ml_storage -target=module.ml_billing -target=module.ml_ingestion_lambda
#      (or infra-deploy.yml's deploy_ml toggle, which also populates the repo
#      variables ml-deploy.yml reads this root's cross-root inputs from).
#
#   2. Training job config + launch script:
#        terraform apply -target=module.training
#        # then ml-train.yml, or the generated launch_training_<run_id>.ps1
#
#   3. Inference endpoint (after a training artifact is in S3):
#        terraform apply -target=module.inference_v3
#
# NOTE: run Terraform via .github/workflows/ml-deploy.yml / ml-train.yml /
# ml-destroy.yml — the Zscaler proxy breaks provider mTLS locally, but not on
# GitHub's runners.

# ── Training: SageMaker Training Job ──────────────────────────────────────────
# Writes training_job_<run_id>.json + launch_training_<run_id>.ps1 into this
# directory. It creates no AWS resources — SageMaker Training Jobs have no
# Terraform resource type — so applying it can never start or bill anything.

module "training" {
  source = "../../modules/training"

  model_name          = var.training_model_name # model_a (Text-to-SQL) | model_c (chart): picks the S3 data prefix and job name
  environment         = var.environment
  run_id              = var.training_run_id
  role_arn            = var.sagemaker_role_arn
  datasets_bucket     = var.datasets_bucket
  artifacts_bucket    = var.artifacts_bucket
  instance_type       = var.training_instance_type
  training_image      = var.training_image
  entry_point         = var.training_entry_point
  use_spot            = var.training_use_spot
  volume_size_gb      = var.training_volume_gb
  max_runtime_seconds = var.training_max_runtime_seconds
  hyperparameters     = var.training_hyperparameters
}

# ── Inference endpoint ────────────────────────────────────────────────────────

# Third generation of the endpoint: `model_a3` -> model-a3-dev. The first two
# (`module.inference` -> model-a-dev, `module.inference_v2` -> model-a2-dev)
# each got stuck waiting for one instance type SageMaker had no capacity for, and
# were replaced under a new address rather than by state surgery.
# They are declared nowhere now;
# ml-destroy.yml's destroy_inference also targets their addresses, so any
# leftovers in state are cleared by the same run.
# The endpoint now takes a priority list of instance types (InstancePools), so
# missing capacity for one type no longer strands it.
module "inference_v3" {
  source = "../../modules/inference"

  model_name            = "model_a3"
  environment           = var.environment
  role_arn              = var.sagemaker_role_arn
  model_artifact_s3_uri = var.inference_artifact_s3_uri != "" ? var.inference_artifact_s3_uri : module.training.artifact_s3_uri
  inference_image       = var.inference_image
  instance_types        = [for t in split(",", var.inference_instance_types) : trimspace(t) if trimspace(t) != ""]
  model_type            = var.inference_model_type
  enable_scale_to_zero  = var.enable_scale_to_zero
  deployment_id         = var.inference_deployment_id
  quant_cache_s3_uri    = var.inference_quant_cache_s3_uri
}

# Backstop for the scale-to-zero above: finishes the job if auto scaling does
# not. Only meaningful when the endpoint scales.
module "inference_watchdog" {
  count  = var.enable_scale_to_zero ? 1 : 0
  source = "../../modules/inference-watchdog"

  environment              = var.environment
  endpoint_name            = module.inference_v3.endpoint_name
  inference_component_name = module.inference_v3.inference_component_name
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "training_run_id" {
  description = "Used by ml-train.yml to find the generated training_job_<run_id>.json"
  value       = var.training_run_id
}

output "artifact_s3_uri" {
  description = "S3 URI the trained model artifact will land at"
  value       = module.training.artifact_s3_uri
}

output "artifacts_bucket" {
  description = "Passed through from the unified root — convenience so scripts only need one `terraform output` call in this root."
  value       = var.artifacts_bucket
}

output "datasets_bucket" {
  value = var.datasets_bucket
}

output "endpoint_name" {
  value = module.inference_v3.endpoint_name
}

output "inference_component_name" {
  description = "Backend ALIBI_SAGEMAKER_INFERENCE_COMPONENT; also needed by any invoke-endpoint call."
  value       = module.inference_v3.inference_component_name
}
