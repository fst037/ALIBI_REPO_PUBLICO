variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

# ── Cross-root inputs ──────────────────────────────────────────────────────────
# IAM/storage/billing/ingestion Lambda are owned by the unified root
# (terraform/envs/dev, module.ml_iam/ml_storage/ml_billing/ml_ingestion_lambda
# — migrated there 2026-07-13). This root previously kept
# its own duplicate copies of those modules with no matching state — fixed
# 2026-09-06 by taking their outputs as plain variables
# instead, the same cross-root-variable pattern used elsewhere in this repo
# (e.g. the database root's outputs feeding the backend Lambda). Fill these in
# from `terraform -chdir=../../../../terraform/envs/dev output <name>`, or
# let ml-deploy.yml populate them automatically via repo variables.

variable "sagemaker_role_arn" {
  description = "From: terraform -chdir=terraform/envs/dev output -raw ml_sagemaker_role_arn"
  type        = string
  default     = ""
}

variable "datasets_bucket" {
  description = "From: terraform -chdir=terraform/envs/dev output -raw ml_datasets_bucket"
  type        = string
  default     = ""
}

variable "artifacts_bucket" {
  description = "From: terraform -chdir=terraform/envs/dev output -raw ml_artifacts_bucket"
  type        = string
  default     = ""
}

# ── Training ──────────────────────────────────────────────────────────────────
# SageMaker is the only backend. The Lambda / EC2-spot / RunPod alternatives and
# the training_backend selector that switched between them were removed
# 2026-09-07 once the ml.g5.2xlarge quota was approved.

variable "training_run_id" {
  description = "Unique ID for this training run. Must change per run: SageMaker rejects duplicate job names and the artifact S3 path is keyed on it."
  type        = string
  default     = "smoke-001"
}

variable "training_entry_point" {
  description = "Path to the training script inside the code tarball, e.g. model_a/train_qlora.py"
  type        = string
  default     = "model_a/train_qlora.py"
}

variable "training_hyperparameters" {
  description = "Passed to the job as SageMaker HyperParameters, and read by the script from SM_HP_<NAME>. sample_fraction = \"0.001\" makes it a smoke run (ML/README.md §12)."
  type        = map(string)
  default     = {}
}

variable "training_instance_type" {
  type    = string
  default = "ml.g5.2xlarge"
}

variable "training_image" {
  description = "ECR URI of the SageMaker training container."
  type        = string
  default     = ""
}

variable "training_use_spot" {
  type    = bool
  default = false
}

variable "training_volume_gb" {
  type    = number
  default = 100
}

variable "training_max_runtime_seconds" {
  type    = number
  default = 43200
}

variable "hf_token" {
  description = "HuggingFace token for gated model downloads. Pass via TF_VAR_hf_token. Qwen2.5-Coder-7B-Instruct is Apache-2.0 and ungated, so this is normally unset."
  type        = string
  sensitive   = true
  default     = ""
}

# ── Inference configuration ───────────────────────────────────────────────────

variable "inference_instance_types" {
  description = "Comma-separated instance types for the models' endpoint, in priority order (InstancePools, 1-5). ml-deploy.yml sets it; a string so the workflow can pass its input as is."
  type        = string
  default     = "ml.g5.2xlarge,ml.g6e.xlarge,ml.g5.4xlarge,ml.g6e.2xlarge,ml.g6.2xlarge"
}

variable "inference_image" {
  description = "ECR URI of the inference container."
  type        = string
  default     = "246618743249.dkr.ecr.us-east-1.amazonaws.com/sagemaker-scikit-learn:1.2-1-cpu-py3"
}

variable "inference_model_type" {
  type    = string
  default = "dummy"
}

variable "inference_quant_cache_s3_uri" {
  description = "s3://bucket/prefix for the handler's quantised-base cache (modules/inference). Empty = off."
  type        = string
  default     = ""
}

variable "enable_scale_to_zero" {
  description = "Models' endpoint scales to 0 when idle (the user's rule: no new fixed costs). See modules/inference."
  type        = bool
  default     = true
}

variable "inference_artifact_s3_uri" {
  description = "Override: pass an explicit S3 URI for the model artifact. If empty, uses the training module's output path."
  type        = string
  default     = ""
}



variable "inference_deployment_id" {
  description = "Label of the training run being served; makes the model/config names unique per run. Set by ml-endpoint.yml."
  type        = string
  default     = ""
}

variable "training_model_name" {
  description = "Which task's data and job name to use: model_a (Text-to-SQL, data under s3://<datasets>/model_a/) or model_c (chart specs, model_c/). Set per run by ml-train.yml's `module` input."
  type        = string
  default     = "model_a"
  validation {
    condition     = contains(["model_a", "model_c"], var.training_model_name)
    error_message = "training_model_name must be model_a or model_c."
  }
}
