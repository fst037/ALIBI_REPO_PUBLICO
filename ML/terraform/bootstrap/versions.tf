terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Local backend — this working dir is never migrated to S3.
  # It bootstraps the S3 bucket that the main workspace will use as its backend.
}

provider "aws" {
  region  = var.aws_region
  profile = "alibi"
}
