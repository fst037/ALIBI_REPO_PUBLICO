output "function_name" {
  description = "Lambda function name for Module B ingestion"
  value       = aws_lambda_function.ingest.function_name
}

output "function_arn" {
  value = aws_lambda_function.ingest.arn
}
