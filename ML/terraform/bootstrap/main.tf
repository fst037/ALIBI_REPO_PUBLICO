# Bootstrap: creates the S3 bucket used as Terraform remote state for the main workspace.
# Run this ONCE from your own terminal:
#
#   cd ml/terraform/bootstrap
#   terraform init
#   terraform apply
#
# After apply, copy the `state_bucket_name` output into ml/terraform/envs/dev/backend.tf,
# then run `terraform init -migrate-state` from ml/terraform/envs/dev/.
# See ml/README.md §1 Bootstrap for the complete step-by-step.

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "tf_state" {
  bucket = "alibi-terraform-state-${data.aws_caller_identity.current.account_id}"

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Project = "alibi"
    Purpose = "terraform-state"
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
