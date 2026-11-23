terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.21 is the first release whose aws_sagemaker_endpoint_configuration
      # takes execution_role_arn and a variant with no model_name — what an
      # endpoint hosting an inference component needs (modules/inference).
      # Only this root moved; terraform/envs/{dev,database} stay on 5.x.
      version = "~> 6.21"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    # module.inference's inference component (AWS::SageMaker::InferenceComponent
    # has no resource in the AWS provider).
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.0"
    }
  }
}

# No `profile`: applied from GitHub Actions (OIDC credentials, no local AWS
# CLI profile on the runner) as well as your own terminal — for local runs,
# set AWS_PROFILE=alibi in your shell instead. Mirrors terraform/envs/dev.
provider "aws" {
  region = var.aws_region
}

provider "awscc" {
  region = var.aws_region
}
