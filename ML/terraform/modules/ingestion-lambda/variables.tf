variable "environment" {
  type    = string
  default = "dev"
}

variable "role_arn" {
  description = "ARN of the Lambda execution role"
  type        = string
}

variable "artifacts_bucket" {
  description = "S3 bucket for embedding indexes and schema inference results"
  type        = string
}

variable "datasets_bucket" {
  description = "S3 bucket where users upload CSV/Excel/JSON files for ingestion"
  type        = string
}

variable "lambda_memory_mb" {
  type    = number
  default = 1024
}

variable "lambda_timeout_seconds" {
  type    = number
  default = 300
}

variable "anthropic_secret_name" {
  description = "Secrets Manager secret name holding the Anthropic API key (under alibi/ prefix)"
  type        = string
  default     = "alibi/anthropic-api-key"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}
