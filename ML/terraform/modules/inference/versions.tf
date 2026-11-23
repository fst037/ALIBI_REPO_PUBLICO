terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Endpoint config with execution_role_arn and no model_name (6.21.0).
      version = ">= 6.21"
    }
    # AWS::SageMaker::InferenceComponent — the AWS provider has no resource for it.
    awscc = {
      source = "hashicorp/awscc"
    }
  }
}
