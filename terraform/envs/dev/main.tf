# ALIBI — unified dev infrastructure (backend + frontend + ML core)
#
# Single `terraform apply` for everything previously split across
# backend/terraform/envs/dev, frontend/terraform/envs/dev, and the
# always-on part of ML/terraform/envs/dev. Modules themselves are untouched
# and still live in their original project directories — this root only
# composes them.
#
# Deliberately NOT included here — both stay fully separate, applied
# manually from your own terminal against ML/terraform/envs/dev, same as
# before this unification:
#   - ML training (module.training) — a training run is a one-off job you
#     kick off deliberately via ml-train.yml, not part of a routine
#     deploy-everything/destroy-everything cycle — see
#     ML/terraform/envs/dev/main.tf.
#   - ML inference (module.inference) — depends on a training run's
#     artifact existing in S3 first; bundling it into the fast
#     deploy-all/destroy-all cycle here would either fail (no artifact yet)
#     or tear down/recreate a real SageMaker endpoint on every cycle.
#
# NOTE: All terraform and aws commands must be run from your own terminal
# (bootstrap/migration only) — the Zscaler proxy breaks provider mTLS in
# subprocess environments. Day-to-day apply/destroy happens via
# .github/workflows/infra-deploy.yml and infra-destroy.yml, which run on
# GitHub's runners (not behind Zscaler).

# ── CI identity (OIDC) ──────────────────────────────────────────────────────────

module "ci_deploy_role" {
  source = "../../modules/ci-deploy-role"

  environment      = var.environment
  state_bucket_arn = "arn:aws:s3:::alibi-terraform-state-213598695817"
  state_key_prefix = "alibi/unified/${var.environment}/"
}

# ── Backend ──────────────────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}

# Names shared by the backend and ingest modules. Derived here rather than
# passed as module outputs in both directions, which would make the graph
# circular: the ingest module needs the backend's code bucket, so the backend
# must not need anything back from it.
locals {
  ingest_bucket           = "alibi-ingest-${var.environment}-${data.aws_caller_identity.current.account_id}"
  processor_function_name = "alibi-ingest-processor-${var.environment}"
}

module "backend" {
  source = "../../../backend/terraform/modules/api-lambda"

  environment = var.environment
  aws_region  = var.aws_region

  allowed_origins        = var.allowed_origins
  lambda_memory_mb       = var.backend_lambda_memory_mb
  lambda_timeout_seconds = var.backend_lambda_timeout_seconds

  # Attach the Lambda to the VPC and point it at the RDS instance so the
  # deployed API reads/writes the real database (see the api-lambda module's
  # db_enabled variable for the password trade-off).
  db_enabled = true
  # Created by terraform/envs/database (module.autostop), named by convention
  # since the two roots share no state. Until that root is applied, the invoke
  # fails and a stopped database is a plain 503.
  db_waker_function_name = "alibi-db-power-${var.environment}"

  # Module B's data plane: DuckDB reads Parquet from S3 through the gateway
  # endpoint the ingest module creates.
  ingest_enabled          = var.ingest_enabled
  ingest_bucket           = local.ingest_bucket
  processor_function_name = local.processor_function_name

  # Module A's SQL model + the orchestrator's narrative model, on Bedrock. With
  # `db_enabled = true` above, turning this on also provisions
  # `module.backend`'s LLM bridge Lambda (outside the VPC) and routes both
  # models' calls through it — see the api-lambda module's `text2sql_enabled` description.
  text2sql_enabled       = var.text2sql_enabled
  text2sql_model_id      = var.text2sql_model_id
  text2sql_prompt_format = var.text2sql_prompt_format
  orchestrator_model_id  = var.orchestrator_model_id
  model_prices_usd       = var.model_prices_usd
  usd_ars_rate           = var.usd_ars_rate
  usd_ars_rate_as_of     = var.usd_ars_rate_as_of

  planner_model_id         = var.planner_model_id
  synthesis_quick_model_id = var.synthesis_quick_model_id
  synthesis_deep_model_id  = var.synthesis_deep_model_id

  # The fine-tuned adapters (Module A SQL, Module C chart) on the SageMaker
  # endpoint ML/terraform/envs/dev deploys — a separate root, so its names are
  # passed by convention (module.inference: "<model>-<env>" and
  # "<model>-<env>-models"), like db_waker_function_name above. The providers
  # say which module actually uses it.
  text2sql_provider              = var.text2sql_provider
  visualization_provider         = var.visualization_provider
  fine_tuned_endpoint_name       = "model-a3-${var.environment}"
  fine_tuned_inference_component = "model-a3-${var.environment}-models"
  # Verification-code email over plain SMTP.
  smtp_user     = var.smtp_user
  smtp_password = var.smtp_password
  # "Continuar con Google".
  google_client_id = var.google_client_id
  # Mercado Pago Checkout Pro, sandbox while the token is a test one.
  mp_access_token = var.mp_access_token
}

# ── Module B — ingestion pipeline (data plane + two-stage worker) ─────────────
#
# The two Lambdas here sit on opposite sides of the VPC boundary on purpose;
# see the module header. Deploying it is a two-step
# the first time only — the processor's container image must exist in ECR before
# the function can be created. See backend/terraform/modules/ingest-pipeline.

module "ingest" {
  count  = var.ingest_enabled ? 1 : 0
  source = "../../../backend/terraform/modules/ingest-pipeline"

  environment = var.environment

  # The browser PUTs straight to this bucket with a presigned URL, so a stale
  # origin here fails the CORS preflight even though the signature is fine.
  # CloudFront's domain changes every time the distribution is recreated, so
  # take it from the frontend module instead of trusting var.allowed_origins to
  # have been edited by hand. Not circular: frontend depends on backend, and
  # backend depends on nothing here.
  allowed_origins = join(",", distinct(concat(
    split(",", var.allowed_origins),
    ["https://${module.frontend.distribution_domain_name}"],
  )))

  # Stage 2 runs the API's own zip with a different handler, so there is no
  # second build and the two cannot drift apart.
  backend_code_bucket = module.backend.code_bucket
  database_url        = module.backend.database_url

  processor_image_tag = var.processor_image_tag

  # Semantic inference. Left at the default (`none`) the pipeline is fully
  # deterministic and calls no model. `bedrock` uses the two model ids below.
  schema_inference_provider = var.schema_inference_provider
  schema_inference_model_id = var.schema_inference_model_id
  embedding_model_id        = var.embedding_model_id
}

# ── Frontend ─────────────────────────────────────────────────────────────────

module "frontend" {
  source = "../../../frontend/terraform/modules/static-site"

  environment = var.environment
  price_class = var.frontend_price_class

  # Route the SPA's /api/v1/* calls to the backend API through CloudFront
  # (same-origin: no CORS, no frontend code change).
  api_gateway_domain = module.backend.api_domain

  # Custom domain (see dns.tf). Empty until the zone is delegated.
  aliases         = local.custom_domain_aliases
  certificate_arn = local.custom_domain_cert_arn
}

# ── ML — core only (no training, no inference — see header comment) ──────────

module "ml_iam" {
  source = "../../../ML/terraform/modules/iam"

  environment = var.environment
}

module "ml_storage" {
  source = "../../../ML/terraform/modules/storage"

  environment = var.environment
}

module "ml_billing" {
  source = "../../../ML/terraform/modules/billing"

  environment       = var.environment
  alert_email       = var.alert_email
  monthly_limit_usd = var.monthly_budget_usd
}

module "ml_ingestion_lambda" {
  source = "../../../ML/terraform/modules/ingestion-lambda"

  environment = var.environment
  role_arn    = module.ml_iam.lambda_role_arn

  artifacts_bucket = module.ml_storage.artifacts_bucket
  datasets_bucket  = module.ml_storage.datasets_bucket
  aws_region       = var.aws_region
}
