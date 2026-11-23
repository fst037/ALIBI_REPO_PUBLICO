output "bucket" {
  description = "Data-plane bucket. Set as ALIBI_INGEST_BUCKET on the API Lambda."
  value       = aws_s3_bucket.ingest.id
}

output "bucket_arn" {
  value = aws_s3_bucket.ingest.arn
}

output "processor_function_name" {
  description = "Set as ALIBI_PROCESSOR_FUNCTION_NAME on the API Lambda (retry path)."
  value       = aws_lambda_function.processor.function_name
}

output "committer_function_name" {
  value = aws_lambda_function.committer.function_name
}

output "ecr_repository_url" {
  description = "Push target for the processor image (backend-deploy.yml)."
  value       = aws_ecr_repository.processor.repository_url
}

output "queue_url" {
  value = aws_sqs_queue.jobs.id
}

output "dlq_url" {
  description = "Anything in here is a bug report — nothing lands in it in normal use."
  value       = aws_sqs_queue.dlq.id
}
