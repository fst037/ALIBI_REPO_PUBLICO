# Consumed by .github/workflows/infra-deploy.yml to auto-populate repo
# variables after apply (see README in that workflow for the mapping).

output "ci_deploy_role_arn" {
  value = module.ci_deploy_role.role_arn
}

# ── Backend ──────────────────────────────────────────────────────────────────

output "backend_function_name" {
  value = module.backend.function_name
}

# The API zip archive. No longer a deploy target — the API is a container image
# The API Lambda is a zip again: removing
# LiteLLM took the package back under Lambda's 250 MB ceiling, and a zip needs
# no ECR repository, no image bootstrap, and no docker build in CI.
output "backend_code_bucket" {
  value = module.backend.code_bucket
}

output "backend_api_endpoint" {
  value = module.backend.api_endpoint
}

# Empty string when text2sql_enabled is off (or db_enabled is, though that's
# not this environment's setup) — see api-lambda's own output. Consumed by
# backend-deploy.yml the same way ingest_committer_function_name is: update
# this function's code from the same zip whenever it exists.
output "llm_bridge_function_name" {
  value = module.backend.llm_bridge_function_name
}

# Same pattern: the answer-run worker runs the API's zip with another handler.
output "run_worker_function_name" {
  value = module.backend.run_worker_function_name
}

# ── Module B — ingestion ─────────────────────────────────────────────────────
# Empty strings (not null) when ingest_enabled is false, so `gh variable set`
# in infra-deploy.yml never chokes on a missing value.

output "ingest_bucket" {
  value = var.ingest_enabled ? module.ingest[0].bucket : ""
}

output "ingest_processor_function_name" {
  value = var.ingest_enabled ? module.ingest[0].processor_function_name : ""
}

output "ingest_committer_function_name" {
  value = var.ingest_enabled ? module.ingest[0].committer_function_name : ""
}

output "ingest_ecr_repository_url" {
  description = "Push target for the processor image in backend-deploy.yml."
  value       = var.ingest_enabled ? module.ingest[0].ecr_repository_url : ""
}

output "ingest_dlq_url" {
  description = "Nothing lands here in normal use — anything that does is a bug report."
  value       = var.ingest_enabled ? module.ingest[0].dlq_url : ""
}

# ── Frontend ─────────────────────────────────────────────────────────────────

output "frontend_bucket_name" {
  value = module.frontend.bucket_name
}

output "frontend_distribution_id" {
  value = module.frontend.distribution_id
}

output "frontend_site_url" {
  value = "https://${module.frontend.distribution_domain_name}"
}

# ── ML ───────────────────────────────────────────────────────────────────────

output "ml_datasets_bucket" {
  value = module.ml_storage.datasets_bucket
}

output "ml_artifacts_bucket" {
  value = module.ml_storage.artifacts_bucket
}

output "ml_sagemaker_role_arn" {
  value = module.ml_iam.role_arn
}

output "ml_lambda_role_arn" {
  value = module.ml_iam.lambda_role_arn
}

# Consumed by ML/terraform/envs/dev (module.training) as
# sagemaker_role_arn/datasets_bucket/artifacts_bucket — that
# module used to duplicate module.ml_iam/ml_storage locally with no matching
# state after this root's 2026-07-13 migration; fixed 2026-09-06 by taking
# these as plain cross-root inputs instead.
output "ml_ingestion_lambda_name" {
  value = module.ml_ingestion_lambda.function_name
}

# ── Custom domain ────────────────────────────────────────────────────────────

# Paste these 4 into nic.ar -> Delegaciones.
output "dns_name_servers" {
  value = local.zone_enabled ? aws_route53_zone.main[0].name_servers : []
}
