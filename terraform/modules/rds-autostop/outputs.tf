output "function_name" {
  description = "Invoke with an empty payload to start the instance (idempotent); the backend's ALIBI_DB_WAKER_FUNCTION_NAME."
  value       = aws_lambda_function.power.function_name
}
