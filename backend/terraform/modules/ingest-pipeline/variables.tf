variable "environment" {
  type    = string
  default = "dev"
}

variable "allowed_origins" {
  description = <<-EOT
    Comma-separated origins allowed to PUT to the bucket from a browser.
    Must include the frontend's CloudFront URL — a presigned URL is not
    enough on its own: without a matching CORS rule the upload fails in the
    browser while working fine from curl.
  EOT
  type        = string
  default     = "http://localhost:5173"
}

variable "raw_retention_days" {
  description = <<-EOT
    How long a raw document survives a FAILED upload. On success the processor
    deletes it immediately — the product rule is that ALIBI keeps the resulting
    table, never the document. This is only the backstop that makes
    "reintentar" honest, and the window after which it stops being offered.
  EOT
  type        = number
  default     = 7
}

# ── Stage 1: processor ─────────────────────────────────────────────────────

variable "processor_image_tag" {
  description = <<-EOT
    ECR tag the processor Lambda runs. The image must exist BEFORE the function
    can be created (Lambda validates it at CreateFunction), which is why the
    first deploy is a two-step: push an image, then apply. CI updates the
    function directly afterwards, and `ignore_changes = [image_uri]` keeps
    Terraform from reverting it.
  EOT
  type        = string
  default     = "bootstrap"
}

variable "processor_memory_mb" {
  description = <<-EOT
    Lambda scales CPU in proportion to memory, and parsing is CPU-bound, so
    this buys speed as much as headroom. 3008 MB is roughly 2 vCPU.
  EOT
  type        = number
  default     = 3008
}

variable "processor_timeout_seconds" {
  description = <<-EOT
    Also used as the SQS visibility timeout — they must not diverge, or SQS
    redelivers a message while the first invocation is still working on it and
    the same file gets processed twice.
  EOT
  type        = number
  default     = 540 # 9 minutes
}

variable "max_rows_per_table" {
  type    = number
  default = 1000000
}

# ── Stage 2: committer ─────────────────────────────────────────────────────

variable "backend_code_bucket" {
  description = <<-EOT
    The API's code bucket. The committer runs the SAME zip with a different
    handler, so there is no second build and the two cannot drift apart.
  EOT
  type        = string
}

variable "database_url" {
  description = <<-EOT
    Postgres DSN, built at apply time from the RDS endpoint + the managed
    password secret — same value and same trade-off as the API Lambda's
    (see backend/terraform/modules/api-lambda/variables.tf). Sensitive, so it is never logged.
  EOT
  type        = string
  sensitive   = true
}

# ── Networking ─────────────────────────────────────────────────────────────

variable "create_s3_endpoint" {
  description = <<-EOT
    Create the S3 Gateway VPC Endpoint. This is what lets in-VPC Lambdas reach
    S3 at all (no NAT exists), and it is free. Set false only if the endpoint
    is already managed elsewhere in the account — having two would conflict on
    the same route tables.
  EOT
  type        = bool
  default     = true
}

# ── Semantic inference (AWS Bedrock) ───────────────────────────────────────
# IAM auth: no key anywhere. ALIBI is AWS-only since 2026-10-05.

variable "schema_inference_provider" {
  description = "`none` (deterministic rules only) or `bedrock`."
  type        = string
  default     = "none"
  validation {
    condition     = contains(["none", "bedrock"], var.schema_inference_provider)
    error_message = "schema_inference_provider must be none or bedrock."
  }
}

variable "schema_inference_model_id" {
  description = "Bedrock model / inference-profile id for column naming and header arbitration."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "embedding_model_id" {
  description = "Bedrock embedding model. Titan Text Embeddings V2 emits embedding_dimensions natively."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "embedding_dimensions" {
  description = "Must match the pgvector column width in migration 0003_module_b."
  type        = number
  default     = 512
}
