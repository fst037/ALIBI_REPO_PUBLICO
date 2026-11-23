variable "environment" {
  type    = string
  default = "dev"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "lambda_memory_mb" {
  type    = number
  default = 512
}

variable "lambda_timeout_seconds" {
  type    = number
  default = 29 # API Gateway HTTP API's hard request timeout
}

variable "allowed_origins" {
  description = "Comma-separated list of origins allowed to call the API (CORS)."
  type        = string
  default     = "http://localhost:5173"
}

# When true, attach the Lambda to the default VPC and point it at the RDS
# instance `alibi-<environment>`: it joins the VPC (so it can reach the private
# database), and `ALIBI_DATABASE_URL` is built at apply time from the RDS
# endpoint + the AWS-managed master-password secret. Left false, the Lambda has
# no VPC config and no DB URL, so the app runs on its in-memory fixtures.
#
# Trade-off: the password ends up in Terraform state and
# the Lambda's env config — avoids a Secrets Manager VPC endpoint (~$7/mo) /
# NAT gateway (~$32/mo) that a runtime secret fetch would need, since a VPC
# Lambda has no internet route.
#
# CONSEQUENCE, and it is not hypothetical: RDS rotates a managed master-user
# secret **every seven days by default**, and this env var is a copy taken at
# apply time. So roughly a week after each apply the deployed API starts failing
# every DB call with "password authentication failed" until someone re-runs
# infra-deploy.yml. backend-deploy.yml cannot fix it — that workflow pushes code
# and never runs Terraform, so it never re-reads the secret. Rotating on a fixed
# schedule while pinning the value at apply time is a standing expiry date on
# the deployment; a durable fix has to remove one side of that (IAM database
# auth, a non-rotating application role, or a runtime fetch).
variable "db_enabled" {
  type    = bool
  default = false
}

# ── Module B ingestion (see backend/terraform/modules/ingest-pipeline) ──────
# Names, not module outputs. Both modules derive them from the same convention,
# which is what keeps the dependency graph acyclic: the ingest module needs this
# one's code bucket, so this one must not need anything back from it.

variable "ingest_enabled" {
  description = <<-EOT
    Point the API at the S3 data plane: Parquet is read with DuckDB (needs the
    S3 Gateway VPC Endpoint the ingest module creates), presigned upload URLs
    are issued against `ingest_bucket`, and a retry re-invokes the processor.
    Left false, the API uses local disk and an in-process thread runner.
  EOT
  type        = bool
  default     = false
}

variable "ingest_bucket" {
  type    = string
  default = ""
}

variable "processor_function_name" {
  description = "Invoked only when a user retries a failed upload."
  type        = string
  default     = ""
}

# ── Module A: Text-to-SQL (see backend/app/services/text2sql/) ──────────────
# Model calls go to AWS Bedrock with IAM auth (ALIBI is AWS-only since
# 2026-10-05): no key, no endpoint URL, nothing outside AWS.

variable "text2sql_enabled" {
  description = <<-EOT
    Turn on the model calls: Module A (writes the SQL) and the orchestrator
    (writes the answer), on Bedrock.

    DEFAULT false. With `db_enabled = true` this Lambda is attached to the VPC
    to reach the private RDS, and no NAT gateway is provisioned, so it has no
    route to Bedrock. In that case (the only one this environment actually
    runs), turning this on ALSO provisions `aws_lambda_function.llm_bridge` — a
    second Lambda outside the VPC that makes the model call — and the Interface
    VPC Endpoint the first Lambda needs to invoke it (`aws_vpc_endpoint.lambda`,
    ~$7.30/mo for one AZ, shared with the RDS waker).

    Left false, `text2sql.is_configured()` returns false and a question is a 503.
  EOT
  type        = bool
  default     = false
}

# terraform/modules/rds-autostop's function, which starts an RDS instance that
# stopped itself while idle. Empty disables it: a stopped database is then a
# plain 503 db_unavailable until someone starts it.
variable "db_waker_function_name" {
  type    = string
  default = ""
}

variable "text2sql_model_id" {
  description = <<-EOT
    Bedrock model or cross-region inference-profile id that writes Module A's
    SQL. A general model stands in (with text2sql_prompt_format =
    "generic-chat-v1") until the fine-tuned Qwen3.8-27B adapter on SageMaker is
    wired in. Model access is IAM (aws_iam_role_policy.bedrock_invoke_*): no key.
  EOT
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "text2sql_prompt_format" {
  description = <<-EOT
    Which prompt shape to send. MUST match how the deployed model was
    fine-tuned — see backend/app/services/text2sql/formats.py. Swapping in a
    retrained model means registering a format there and changing this value;
    nothing else in the backend changes.
  EOT
  type        = string
  # generic-chat-v1 because the default text2sql_model_id is a general model.
  # sql-create-context-v1 was the prompt of the first (Azure-trained) qwen3
  # fine-tune, retired with Azure on 2026-10-05.
  default = "generic-chat-v1"
}

variable "text2sql_provider" {
  description = <<-EOT
    Who writes Module A's SQL: "bedrock" (text2sql_model_id, a general model) or
    "sagemaker" (the fine-tuned Qwen3.8-27B adapter on fine_tuned_endpoint_name,
    which needs text2sql_prompt_format = "qwen-bird-spider-v1").
  EOT
  type        = string
  default     = "bedrock"
  validation {
    condition     = contains(["bedrock", "sagemaker"], var.text2sql_provider)
    error_message = "text2sql_provider must be bedrock or sagemaker."
  }
}

variable "visualization_provider" {
  description = "Who writes Module C's chart: \"bedrock\" (prompted, orchestrator_model_id) or \"sagemaker\" (the fine-tuned nvBench adapter on the same endpoint as Module A's)."
  type        = string
  default     = "bedrock"
  validation {
    condition     = contains(["bedrock", "sagemaker"], var.visualization_provider)
    error_message = "visualization_provider must be bedrock or sagemaker."
  }
}

variable "fine_tuned_endpoint_name" {
  description = "The SageMaker endpoint serving the fine-tuned adapters (ML/terraform/envs/dev output endpoint_name). Empty = none deployed."
  type        = string
  default     = ""
}

variable "fine_tuned_inference_component" {
  description = "Its inference component (ML/terraform/envs/dev output inference_component_name): what invocations name, and what the backend wakes after a scale-to-zero."
  type        = string
  default     = ""
}

variable "model_wake_wait_seconds" {
  description = "How long a run waits for the scaled-to-zero model to come back before answering 503 model_waking. Must leave the answer time inside run_worker_timeout_seconds."
  type        = number
  default     = 720
}

variable "llm_bridge_timeout_seconds" {
  description = <<-EOT
    Timeout for `aws_lambda_function.llm_bridge` (only created when
    `text2sql_enabled && db_enabled`). Sized for the agent harness's longest
    single call — the Deep synthesis, up to ALIBI_SYNTHESIS_TIMEOUT_SECONDS
    (120 s) — which runs inside the 15-minute run worker. The synchronous
    API's calls stay bounded by their own per-call timeouts (15-20 s, under API
    Gateway's 29 s), and the caller's read timeout follows each call's own
    (app/core/llm_bridge_transport.py), so a longer ceiling here costs nothing
    on that path.
  EOT
  type        = number
  default     = 150
}

# ---------------------------------------------------------------------------
# Orchestrator — narrative synthesis (app/orchestrator)
#
# The general model that WRITES the answer (and, until Module C's adapter is
# wired in, the prompted chart), as opposed to Module A's, which writes the
# query. Gated by `text2sql_enabled` alongside Module A (see main.tf).
# AWS Bedrock, IAM auth — ALIBI is AWS-only since 2026-10-05.
# ---------------------------------------------------------------------------

variable "orchestrator_model_id" {
  description = "Bedrock model or cross-region inference-profile id for the narrative and the prompted chart."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "model_prices_usd" {
  description = <<-EOT
    USD per 1M tokens for each model id above, as {input, output}. Every
    answered question is written to the cost ledger at these prices
    (app/core/usage_meter.py); a model missing here is metered at 0. Keep it in
    step with text2sql_model_id / orchestrator_model_id and the Bedrock pricing
    page for the region.
  EOT
  type        = map(object({ input = number, output = number }))
  default = {
    "us.anthropic.claude-haiku-4-5-20251001-v1:0" = { input = 1.0, output = 5.0 }
    # Sonnet writes Deep answers (Quick ones are Haiku, priced above); Opus is
    # priced in case a deploy chooses it for Deep.
    "us.anthropic.claude-sonnet-4-6"  = { input = 3.0, output = 15.0 }
    "us.anthropic.claude-opus-4-6-v1" = { input = 5.0, output = 25.0 }
  }
}

variable "run_worker_timeout_seconds" {
  description = <<-EOT
    Timeout of the answer-run worker (app/run_worker.py). Lambda's maximum, 900 s:
    a Deep answer runs several queries and a large synthesis model. Keep
    ALIBI_RUN_STALE_AFTER_SECONDS (960 by default) a little above it — that is
    when a run whose worker was killed starts reading as failed.
  EOT
  type        = number
  default     = 900
}

variable "usd_ars_rate" {
  description = <<-EOT
    Official ARS per USD. Plan prices are USD reference values shown and charged
    in pesos at this rate (app/core/pricing.py); the cost ledger converts with it
    too. Update it, with usd_ars_rate_as_of, when the official rate moves.
  EOT
  type        = number
  default     = 1550
}

variable "usd_ars_rate_as_of" {
  description = "ISO date of usd_ars_rate, shown next to the prices."
  type        = string
  default     = "2026-10-01"
}

# ---------------------------------------------------------------------------
# The agent harness (app/orchestrator/harness.py)
#
# A small fast planner drives the loop; a bigger model writes the synthesis,
# Haiku for Quick and Sonnet for Deep. Gated by `text2sql_enabled` with the
# other models: a question needs all of them, and none falls back.
# ---------------------------------------------------------------------------

variable "planner_model_id" {
  description = "Bedrock model or inference-profile id of the harness's planner (one call per turn: latency matters most)."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "synthesis_quick_model_id" {
  description = "Bedrock model or inference-profile id that writes a Quick answer (Haiku: the business model's cost per query)."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "synthesis_deep_model_id" {
  description = "Bedrock model or inference-profile id that writes a Deep answer (Pro only)."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-6"
}

# ── Verification-code email (app/core/mailer.py) ─────────────────────────────
# Plain SMTP from the project's own mailbox — no mail service. Set on whichever function opens the SMTP connection: the LLM
# bridge in the VPC topology (the API Lambda has no route out), the API Lambda
# itself otherwise. An empty password is the app's dev mode: the code is only
# logged, which in a deploy means nobody can finish registering.

variable "smtp_user" {
  description = "SMTP login, and the From address of the code email (e.g. the project's Gmail address)."
  type        = string
  default     = ""
}

variable "smtp_password" {
  description = "SMTP password — for Gmail, an app password (requires 2-Step Verification on the account)."
  type        = string
  default     = ""
  sensitive   = true
}

# ── Mercado Pago Checkout Pro (app/core/mercadopago.py) ──────────────────────
# The access token of a Mercado Pago seller application. A TEST token (the
# application's "credenciales de prueba") is the sandbox: test cards, no money
# moves. A secret. The call itself runs on the LLM bridge (api.mercadopago.com
# is out of the VPC's reach); the API passes the token in the payload, so only
# the API needs it. Empty = paying for a plan is unavailable (503
# payments_unavailable); Free is unaffected.
variable "mp_access_token" {
  description = "Mercado Pago access token (test credentials = sandbox). Empty disables paid checkout."
  type        = string
  default     = ""
  sensitive   = true
}

# ── "Continuar con Google" (app/core/google_identity.py) ─────────────────────
# The OAuth client id from the Google Cloud console — the audience every ID
# token must carry. Not a secret: the frontend
# bakes the same value in as VITE_GOOGLE_CLIENT_ID. The check itself runs on the
# LLM bridge (it needs Google's public keys, out of the VPC's reach), but the
# API passes the audience in the payload, so only the API needs this. Empty =
# no Google sign-in (503 google_unavailable); email and password still work.

variable "google_client_id" {
  description = "Google OAuth client id for Sign in with Google (empty disables it)."
  type        = string
  default     = ""
}
