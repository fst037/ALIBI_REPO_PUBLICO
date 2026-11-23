output "state_bucket_name" {
  description = "Paste this value into ml/terraform/envs/dev/backend.tf before running terraform init -migrate-state."
  value       = aws_s3_bucket.tf_state.bucket
}

output "state_bucket_arn" {
  value = aws_s3_bucket.tf_state.arn
}
