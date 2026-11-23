aws_region  = "us-east-1"
environment = "dev"

# ─────────────────────────────────────────────────────────────────────────────
# Cross-root inputs — IAM/storage/billing/ingestion Lambda are owned by the
# unified root (terraform/envs/dev), not this one. Fill these in from:
#   terraform -chdir=../../../../terraform/envs/dev output -raw ml_sagemaker_role_arn
#   terraform -chdir=../../../../terraform/envs/dev output -raw ml_datasets_bucket
#   terraform -chdir=../../../../terraform/envs/dev output -raw ml_artifacts_bucket
# ml-deploy.yml / ml-train.yml populate these automatically as TF_VAR_* from
# repo variables. Do NOT set them to "" here: terraform.tfvars outranks
# TF_VAR_* in Terraform's precedence, so a blank value silently overrides the
# real one and every generated S3 URI comes out as "s3:///..." (2026-10-04).
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Training — SageMaker on-demand ml.g5.2xlarge. The quota was approved
# 2026-09-06; the spot quota (L-CAEE7DB7) is still pending, so use_spot stays
# false. The Lambda / EC2-spot / RunPod backends that used to live alongside
# this were removed 2026-09-07.
# ─────────────────────────────────────────────────────────────────────────────
training_run_id      = "smoke-002" # bump per run; SageMaker rejects duplicate job names
training_entry_point = "model_a/train_qlora.py"

# SMOKE RUN: sample_fraction = "0.001" trains on 0.1% of the same data through
# the identical script/image/backend — minutes and cents instead of 6-10h and
# ~$14-24. It reaches train_qlora.py as SM_HP_SAMPLE_FRACTION (the same seam
# every other hyperparameter here already uses). See ML/README.md §6.
# For the real run: drop sample_fraction (or set "1.0"), set training_run_id to
# something like "qlora-001", and raise training_max_runtime_seconds to 43200.
# lora_r / lora_alpha / max_seq_length / model class use the script defaults, which are the
# settings validated for Qwen3.8-27B on one A10G (train_qlora.py). task = sql | chart.
training_hyperparameters = { epochs = "3", learning_rate = "2e-4", sample_fraction = "0.001", task = "sql" }

training_instance_type       = "ml.g5.2xlarge"
training_image               = "763104351884.dkr.ecr.us-east-1.amazonaws.com/huggingface-pytorch-training:2.9.0-transformers5.3.0-gpu-py312-cu130-ubuntu22.04-v1.0"
training_use_spot            = false
training_volume_gb           = 100
training_max_runtime_seconds = 5400 # 90m smoke-run ceiling; 43200 (12h) for the real run

# HuggingFace token, only needed for gated models (Qwen2.5-Coder-7B-Instruct
# is Apache-2.0 and ungated, so normally unset):
#   $env:TF_VAR_hf_token = "hf_..."

# ── Inference endpoint ────────────────────────────────────────────────────────
inference_image      = "763104351884.dkr.ecr.us-east-1.amazonaws.com/pytorch-inference:2.2.0-cpu-py310-ubuntu20.04-sagemaker"
inference_model_type = "dummy"
enable_scale_to_zero = true
