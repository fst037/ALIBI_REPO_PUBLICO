output "datasets_bucket" {
  description = "Name of the datasets S3 bucket. Pass to training module and download_datasets.sh."
  value       = aws_s3_bucket.datasets.bucket
}

output "artifacts_bucket" {
  description = "Name of the model artifacts S3 bucket. Pass to training and inference modules."
  value       = aws_s3_bucket.artifacts.bucket
}

output "datasets_bucket_arn" {
  value = aws_s3_bucket.datasets.arn
}

output "artifacts_bucket_arn" {
  value = aws_s3_bucket.artifacts.arn
}
