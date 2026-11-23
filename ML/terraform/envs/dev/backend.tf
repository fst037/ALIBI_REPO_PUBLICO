# State lives in the bucket created by ml/terraform/bootstrap/.
#
# No `profile`: this root runs in GitHub Actions (.github/workflows/ml-deploy.yml,
# OIDC-supplied credentials, no local AWS CLI profile on the runner) as well as
# from your own terminal. For local runs, set AWS_PROFILE=alibi in your shell
# instead — see versions.tf.
terraform {
  backend "s3" {
    bucket = "alibi-terraform-state-213598695817"
    key    = "alibi/ml/dev/terraform.tfstate"
    region = "us-east-1"
  }
}
