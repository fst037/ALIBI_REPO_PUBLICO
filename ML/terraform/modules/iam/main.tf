data "aws_caller_identity" "current" {}
locals {
  account_id = data.aws_caller_identity.current.account_id
}

data "aws_iam_policy_document" "sagemaker_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sagemaker" {
  name               = "AlibiSageMakerRole-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.sagemaker_assume.json
  description        = "Execution role for ALIBI SageMaker training jobs and inference endpoints"
}

# Managed policy for SageMaker core operations (training, endpoints, experiments, logs)
resource "aws_iam_role_policy_attachment" "sagemaker_full" {
  role       = aws_iam_role.sagemaker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess"
}

# Required for pulling AWS DLC images (hosted in cross-account ECR, account 246618743249).
# AmazonSageMakerFullAccess alone does not grant cross-account ecr:BatchGetImage etc.
resource "aws_iam_role_policy_attachment" "sagemaker_ecr" {
  role       = aws_iam_role.sagemaker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# Least-privilege inline policy: S3 access scoped to ALIBI buckets only
resource "aws_iam_role_policy" "s3_alibi" {
  name = "AlibiS3Access-${var.environment}"
  role = aws_iam_role.sagemaker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketLocation",
        ]
        Resource = [
          "arn:aws:s3:::alibi-datasets-${var.environment}-*",
          "arn:aws:s3:::alibi-datasets-${var.environment}-*/*",
          "arn:aws:s3:::alibi-model-artifacts-${var.environment}-*",
          "arn:aws:s3:::alibi-model-artifacts-${var.environment}-*/*",
        ]
      }
    ]
  })
}

# Inline policy: Secrets Manager read access scoped to alibi/ prefix
resource "aws_iam_role_policy" "secrets" {
  name = "AlibiSecretsAccess-${var.environment}"
  role = aws_iam_role.sagemaker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = ["arn:aws:secretsmanager:*:*:secret:alibi/*"]
      }
    ]
  })
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda execution role — shared by training-lambda and ingestion-lambda
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "AlibiLambdaRole-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  description        = "Execution role for ALIBI Lambda functions (training + Module B ingestion)"
}

# Basic Lambda execution: CloudWatch Logs
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# S3 access scoped to ALIBI buckets
resource "aws_iam_role_policy" "lambda_s3" {
  name = "AlibiLambdaS3-${var.environment}"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
          "s3:GetBucketLocation",
        ]
        Resource = [
          "arn:aws:s3:::alibi-datasets-${var.environment}-*",
          "arn:aws:s3:::alibi-datasets-${var.environment}-*/*",
          "arn:aws:s3:::alibi-model-artifacts-${var.environment}-*",
          "arn:aws:s3:::alibi-model-artifacts-${var.environment}-*/*",
        ]
      }
    ]
  })
}

# Secrets Manager: read alibi/* secrets (HuggingFace token, Anthropic API key)
resource "aws_iam_role_policy" "lambda_secrets" {
  name = "AlibiLambdaSecrets-${var.environment}"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = ["arn:aws:secretsmanager:*:*:secret:alibi/*"]
      }
    ]
  })
}

# Bedrock: invoke Titan Embeddings V2 (Module B ingestion)
resource "aws_iam_role_policy" "lambda_bedrock" {
  name = "AlibiLambdaBedrock-${var.environment}"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["bedrock:InvokeModel"]
        Resource = [
          "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0",
        ]
      }
    ]
  })
}
