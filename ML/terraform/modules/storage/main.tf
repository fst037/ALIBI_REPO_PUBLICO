data "aws_caller_identity" "current" {}

locals {
  account_suffix = data.aws_caller_identity.current.account_id
}

# ── Datasets bucket ───────────────────────────────────────────────────────────
resource "aws_s3_bucket" "datasets" {
  bucket = "alibi-datasets-${var.environment}-${local.account_suffix}"
  tags   = { Project = "alibi", Environment = var.environment, Purpose = "datasets" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "datasets" {
  bucket = aws_s3_bucket.datasets.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "datasets" {
  bucket                  = aws_s3_bucket.datasets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Model artifacts bucket ────────────────────────────────────────────────────
resource "aws_s3_bucket" "artifacts" {
  bucket = "alibi-model-artifacts-${var.environment}-${local.account_suffix}"
  tags   = { Project = "alibi", Environment = var.environment, Purpose = "model-artifacts" }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Clean up incomplete multipart uploads after 7 days
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "abort-incomplete-mpu"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}
