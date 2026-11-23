output "function_name" {
  value = aws_lambda_function.api.function_name
}

output "function_arn" {
  value = aws_lambda_function.api.arn
}

output "code_bucket" {
  value = aws_s3_bucket.code.id
}

output "api_endpoint" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

# Host only (no scheme/path) — for use as a CloudFront origin domain so the
# frontend can route /api/v1/* to the API same-origin.
output "api_domain" {
  value = replace(aws_apigatewayv2_api.api.api_endpoint, "https://", "")
}

# The DSN this module already builds from the RDS endpoint + managed-password
# secret. Exposed so the stage-2 committer connects with exactly the same
# credentials rather than assembling its own copy of the same logic.
output "database_url" {
  value     = local.db_dsn
  sensitive = true
}

output "lambda_role_arn" {
  value = aws_iam_role.lambda.arn
}

# Empty when text2sql_enabled/db_enabled aren't both true (the only case that
# creates it) — see the LLM bridge resources above. `backend-deploy.yml` skips
# updating its code when this is empty, same pattern as
# ingest_committer_function_name.
output "llm_bridge_function_name" {
  value = var.text2sql_enabled && var.db_enabled ? aws_lambda_function.llm_bridge[0].function_name : ""
}

output "api_execution_arn" {
  value = aws_apigatewayv2_api.api.execution_arn
}

# Empty when text2sql_enabled is off. backend-deploy.yml updates this function's
# code alongside the API's (same zip) and skips it when this is empty.
output "run_worker_function_name" {
  value = local.run_worker_enabled ? aws_lambda_function.run_worker[0].function_name : ""
}
