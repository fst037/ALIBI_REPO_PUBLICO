output "role_arn" {
  description = "ARN of the SageMaker execution role. Pass to training and inference modules."
  value       = aws_iam_role.sagemaker.arn
}

output "role_name" {
  value = aws_iam_role.sagemaker.name
}

output "lambda_role_arn" {
  description = "ARN of the Lambda execution role (training + ingestion Lambdas)"
  value       = aws_iam_role.lambda.arn
}
