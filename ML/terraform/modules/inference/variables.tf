variable "model_name" {
  description = "Identifier for the model (e.g., 'model_a')"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, prod)"
  type        = string
}

variable "role_arn" {
  description = "ARN of the SageMaker execution role"
  type        = string
}

variable "model_artifact_s3_uri" {
  description = "S3 URI of model.tar.gz produced by the training job. Get from: terraform output artifact_s3_uri"
  type        = string
}

variable "inference_image" {
  description = "ECR URI of the inference container. Dry run: sklearn DLC. QLoRA: HuggingFace PyTorch inference DLC."
  type        = string
}

variable "instance_types" {
  description = <<-EOT
    Instance types the endpoint may run on, in priority order (InstancePools, 1-5
    entries). SageMaker takes the first one with capacity. Every entry must fit the
    handler: one GPU with 24 GB or more for the 4-bit 27B.
  EOT
  type = list(string)

  validation {
    condition     = length(var.instance_types) >= 1 && length(var.instance_types) <= 5 && length(distinct(var.instance_types)) == length(var.instance_types)
    error_message = "instance_types takes 1 to 5 distinct instance types (SageMaker's InstancePools limit)."
  }
}

variable "model_type" {
  description = "Passed to inference.py as MODEL_TYPE env var. 'dummy' for the dry run, 'qlora' for the fine-tuned model."
  type        = string
  default     = "dummy"
}

variable "quant_cache_s3_uri" {
  description = <<-EOT
    s3://bucket/prefix where the handler keeps the 4-bit-quantised base model.
    The first cold start saves it; later ones download it instead of fetching ~55 GB
    from HuggingFace and quantising it (~10 min of GPU time -> a few minutes). Empty
    disables the cache. The SageMaker role already reads/writes the artifacts bucket.
  EOT
  type        = string
  default     = ""
}

variable "enable_scale_to_zero" {
  description = "Scale the models' inference component to 0 copies (and the endpoint to 0 instances) when idle, and back up on demand. False keeps one copy always on (billing every hour)."
  type        = bool
  default     = true
}

variable "scale_in_cooldown_seconds" {
  description = "Target-tracking scale-in cooldown. The idle time before scaling to 0 is set mostly by the policy's low alarm (~15 one-minute periods with no invocations); this only spaces successive scale-ins."
  type        = number
  default     = 300
}

variable "min_memory_mb" {
  description = <<-EOT
    Host memory SageMaker reserves for the inference component when placing a
    copy — a placement floor, not a limit on what the container may use. Keep it
    small: 8192 on ml.g6e.xlarge (32 GiB) was refused with "not enough hardware
    resources" on 2026-10-12, while 1024 placed and the 27B loaded and answered.
   The GPU, not this, is what the copy actually needs.
  EOT
  type        = number
  default     = 1024
}

variable "deployment_id" {
  description = "Short label of the training run being served (e.g. the run id). Appended to the SageMaker model and endpoint-config names so switching runs replaces them cleanly (create-before-destroy) while the endpoint keeps one stable name. Empty keeps the legacy names."
  type        = string
  default     = ""
}

variable "startup_timeout_seconds" {
  description = "How long SageMaker waits for the container to become healthy. The handler downloads the base model from HuggingFace at startup (~15 GB for 7B, ~55 GB for 27B) before it can answer, so the 600 s default is not enough."
  type        = number
  default     = 1800
}
