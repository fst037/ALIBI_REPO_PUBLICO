variable "model_name" {
  description = "Identifier for the model being trained (e.g., 'model_a')"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev, prod)"
  type        = string
}

variable "run_id" {
  description = "Unique identifier for this training run. Use 'phase0' for the dry run; a timestamp or experiment name for real runs."
  type        = string
  default     = "phase0"
}

variable "role_arn" {
  description = "ARN of the SageMaker execution role (output of iam module)"
  type        = string
}

variable "datasets_bucket" {
  description = "S3 bucket name for training datasets (output of storage module)"
  type        = string
}

variable "artifacts_bucket" {
  description = "S3 bucket name for model artifacts, checkpoints, and packaged code (output of storage module)"
  type        = string
}

variable "instance_type" {
  description = "SageMaker training instance type. Dry run: ml.m5.xlarge (CPU, no GPU quota needed). QLoRA: ml.g5.2xlarge."
  type        = string
  default     = "ml.m5.xlarge"
}

variable "training_image" {
  description = "ECR URI of the training container image. Dry run: sklearn DLC. QLoRA: HuggingFace PyTorch DLC."
  type        = string
}

variable "entry_point" {
  description = "Training script filename, relative to the packaged source dir. Dry run: train_dummy.py. QLoRA: train_qlora.py."
  type        = string
  default     = "train_dummy.py"
}

variable "use_spot" {
  description = "Enable Managed Spot Training. Set false for the CPU dry run (cheap, no benefit). Set true for GPU training (essential for cost control)."
  type        = bool
  default     = false
}

variable "volume_size_gb" {
  description = "EBS volume size in GB for the training instance. 50 GB for the dry run; 100 GB for QLoRA (model weights + datasets)."
  type        = number
  default     = 50
}

variable "max_runtime_seconds" {
  description = "Max total runtime before the job is killed. Dry run: 1800 (30 min). QLoRA: 43200 (12 h)."
  type        = number
  default     = 1800
}

variable "hyperparameters" {
  description = "Additional hyperparameters passed to the training script (merged with sagemaker_program and sagemaker_submit_directory)."
  type        = map(string)
  default     = {}
}

variable "code_name" {
  description = "Name of the shared code tarball in the artifacts bucket (code/<code_name>.tar.gz). One tarball (model_a/ + model_c/ + common/) serves every task, so this stays 'model_a' even when model_name is 'model_c'."
  type        = string
  default     = "model_a"
}
