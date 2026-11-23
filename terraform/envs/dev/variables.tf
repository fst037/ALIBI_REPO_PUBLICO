# ── Shared ─────────────────────────────────────────────────────────────────────

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

# ── Backend (api-lambda) ──────────────────────────────────────────────────────

variable "allowed_origins" {
  description = "Comma-separated CORS origins for the API. Includes the CloudFront frontend URL once known."
  type        = string
  default     = "http://localhost:5173"
}

variable "backend_lambda_memory_mb" {
  type    = number
  default = 512
}

variable "backend_lambda_timeout_seconds" {
  type    = number
  default = 29
}

# ── Module B — ingestion pipeline ────────────────────────────────────────────

variable "ingest_enabled" {
  description = <<-EOT
    Provision Module B's data plane (S3 + SQS + the two-stage worker) and point
    the API at it. Kept behind a flag because the FIRST apply with it on needs
    the processor's container image pushed to ECR beforehand — Lambda validates
    the image at CreateFunction. See backend/terraform/modules/ingest-pipeline.
  EOT
  type        = bool
  default     = false
}

variable "processor_image_tag" {
  description = "ECR tag the processor Lambda runs. CI updates it directly afterwards."
  type        = string
  default     = "bootstrap"
}

# Semantic inference (Module B) on AWS Bedrock — IAM auth, no key anywhere.
# ALIBI is AWS-only since 2026-10-05.

variable "schema_inference_provider" {
  description = "`none` (deterministic rules only, the default) or `bedrock`."
  type        = string
  default     = "none"
}

variable "schema_inference_model_id" {
  type    = string
  default = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "embedding_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

# ── Module A (Text-to-SQL) + orchestrator, on Bedrock ─────────────────────────
# Model ids only: Bedrock authorises the calling Lambda's IAM role, so there is
# no key to pass, store or rotate.

variable "text2sql_enabled" {
  description = <<-EOT
    Turns on the model calls (Module A + the orchestrator, on Bedrock). With
    db_enabled (hardcoded true in main.tf) it also provisions the bridge Lambda
    the VPC-attached API reaches Bedrock through — see the api-lambda module.
  EOT
  type        = bool
  default     = false
}

variable "text2sql_model_id" {
  description = "Writes Module A's SQL when text2sql_provider is bedrock."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "text2sql_prompt_format" {
  description = "Must match the model in text2sql_model_id — see app/services/text2sql/formats.py."
  type        = string
  default     = "generic-chat-v1"
}

variable "text2sql_provider" {
  description = <<-EOT
    "bedrock": text2sql_model_id writes the SQL. "sagemaker": the fine-tuned
    Qwen3.8-27B adapter does — set text2sql_prompt_format = "qwen-bird-spider-v1"
    with it, and deploy the endpoint first (ml-deploy.yml).
  EOT
  type        = string
  default     = "bedrock"
}

variable "visualization_provider" {
  description = "\"bedrock\": Module C is prompted (orchestrator_model_id). \"sagemaker\": the fine-tuned nvBench adapter on the same endpoint."
  type        = string
  default     = "bedrock"
}

# ── Custom domain (dns.tf) ───────────────────────────────────────────────────

variable "domain_name" {
  description = "Apex domain registered at NIC Argentina. Empty = no Route 53 zone, no custom domain."
  type        = string
  default     = ""
}

variable "domain_delegated" {
  description = <<-EOT
    Set true ONLY after the Route 53 name servers are delegated at nic.ar and
    `nslookup -type=NS <domain>` returns them. Turns on the ACM certificate
    (whose DNS validation cannot succeed before that) and wires CloudFront.
  EOT
  type        = bool
  default     = false
}

variable "domain_include_www" {
  type    = bool
  default = true
}

# ── Frontend (static-site) ────────────────────────────────────────────────────

variable "frontend_price_class" {
  description = "CloudFront price class. PriceClass_100 = US/Canada/Europe only (cheapest)."
  type        = string
  default     = "PriceClass_100"
}

# ── ML — core only (no training, no inference — see main.tf header) ──────────

variable "alert_email" {
  description = "Email address for ML budget alert notifications. Set in terraform.tfvars (persisted there; do not pass -var)."
  type        = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 200
}

# ---------------------------------------------------------------------------
# Orchestrator — narrative synthesis (and the prompted chart). Gated by
# `text2sql_enabled` alongside Module A: a conversation needs both to answer.
# ---------------------------------------------------------------------------

variable "orchestrator_model_id" {
  type    = string
  default = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "model_prices_usd" {
  description = "USD per 1M tokens per model id, for the cost ledger — see the api-lambda module."
  type        = map(object({ input = number, output = number }))
  default = {
    "us.anthropic.claude-haiku-4-5-20251001-v1:0" = { input = 1.0, output = 5.0 }
    # Sonnet writes Deep answers (Quick ones are Haiku, priced above); Opus is
    # priced in case a deploy chooses it for Deep.
    "us.anthropic.claude-sonnet-4-6"  = { input = 3.0, output = 15.0 }
    "us.anthropic.claude-opus-4-6-v1" = { input = 5.0, output = 25.0 }
  }
}


variable "usd_ars_rate" {
  description = "Official ARS per USD for plan prices and the cost ledger — see the api-lambda module."
  type        = number
  default     = 1550
}

variable "usd_ars_rate_as_of" {
  description = "ISO date of usd_ars_rate."
  type        = string
  default     = "2026-10-01"
}

# The agent harness: a fast planner, and the model that writes the answer —
# Haiku for Quick, Sonnet for Deep. Ids are the
# `us.` cross-region inference profiles listed on this account.
variable "planner_model_id" {
  type    = string
  default = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "synthesis_quick_model_id" {
  type    = string
  default = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "synthesis_deep_model_id" {
  type    = string
  default = "us.anthropic.claude-sonnet-4-6"
}


# Verification-code email — see backend/terraform/modules/api-lambda/variables.tf.
# Passed by infra-deploy.yml from the repo variable ALIBI_SMTP_USER and the
# secret ALIBI_SMTP_PASSWORD; never written to terraform.tfvars.
variable "smtp_user" {
  type    = string
  default = ""
}

variable "smtp_password" {
  type      = string
  default   = ""
  sensitive = true
}

# Mercado Pago Checkout Pro — see backend/terraform/modules/api-lambda/variables.tf.
# Passed by infra-deploy.yml from the secret ALIBI_MP_ACCESS_TOKEN (a TEST
# token while this is a sandbox); never written to terraform.tfvars.
variable "mp_access_token" {
  type      = string
  default   = ""
  sensitive = true
}

# "Continuar con Google" — see backend/terraform/modules/api-lambda/variables.tf.
# Passed by infra-deploy.yml from the repo variable ALIBI_GOOGLE_CLIENT_ID, the
# same one frontend-deploy.yml bakes into the build as VITE_GOOGLE_CLIENT_ID.
variable "google_client_id" {
  type    = string
  default = ""
}
