terraform {
  # 1.7, not 1.6: backend/terraform/modules/api-lambda uses a `removed` block to
  # drop the old deployment zip from state without deleting the object, and
  # `removed` was introduced in 1.7. CI pins 1.9.8.
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

# No hardcoded `profile`: unlike backend/frontend/ML's original per-project
# roots (local-terminal-only), this unified root also runs in GitHub Actions
# via infra-deploy.yml/infra-destroy.yml, which supply credentials through
# OIDC (aws-actions/configure-aws-credentials) as env vars, not a named AWS
# CLI profile — there's no ~/.aws/credentials file on the runner. For local
# runs (bootstrap, migration, one-off `terraform plan`), set AWS_PROFILE=alibi
# in your shell before running terraform commands instead:
#   cmd:        set AWS_PROFILE=alibi
#   PowerShell: $env:AWS_PROFILE = "alibi"
provider "aws" {
  region = var.aws_region
}
