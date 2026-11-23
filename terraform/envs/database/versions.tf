terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

# Same pattern as terraform/envs/dev/versions.tf: no hardcoded `profile`
# since this root is applied via .github/workflows/db-deploy.yml (OIDC
# credentials on the runner), not local `terraform apply` — the Zscaler
# proxy on this machine breaks provider mTLS for local runs. For a local
# `terraform plan` set AWS_PROFILE=alibi in your shell first.
provider "aws" {
  region = var.aws_region
}
